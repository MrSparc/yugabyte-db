#!/usr/bin/env python2

# Copyright (c) YugaByte, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License.  You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License
# is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied.  See the License for the specific language governing permissions and limitations
# under the License.

"""
A wrapper script around PostgreSQL build that allows building debug and release versions in separate
directories.
"""

import os
import logging
import argparse
import re
import sys
import multiprocessing
import subprocess
import json
import hashlib

from subprocess import check_call

from yugabyte_pycommon import init_logging, run_program, WorkDirContext, mkdir_p, quote_for_bash, \
        is_verbose_mode

from yb import common_util
from yb.common_util import YB_SRC_ROOT, get_build_type_from_build_root, get_bool_env_var


REMOVE_CONFIG_CACHE_MSG_RE = re.compile(r'error: run.*\brm config[.]cache\b.*and start over')

ALLOW_REMOTE_COMPILATION = False

CONFIG_ENV_VARS = [
    'CFLAGS',
    'CXXFLAGS',
    'LDFLAGS',
    'PATH',
    'YB_BUILD_ROOT',
    'YB_BUILD_TYPE',
    'YB_SRC_ROOT',
    'YB_THIRDPARTY_DIR'
]


def sha256(s):
    return hashlib.sha256(s).hexdigest()


def adjust_error_on_warning_flag(flag, step, language):
    """
    Adjust a given compiler flag according to whether this is for configure or make.
    """
    assert language in ('c', 'c++')
    assert step in ('configure', 'make')
    if language == 'c' and flag in ('-Wreorder', '-Wnon-virtual-dtor'):
        # Skip C++-only flags.
        return None

    if step == 'configure':
        if flag == '-Werror':
            # Skip this flag altogether during the configure step.
            return None

        if flag == '-fsanitize=thread':
            # Don't actually enable TSAN in the configure step, otherwise configure will think that
            # our pthread library is not working properly.
            # https://gist.githubusercontent.com/mbautin/366970ac55c9d3579816d5e8563e70b4/raw
            return None

    if step == 'make':
        # No changes.
        return flag

    if flag.startswith('-Werror='):
        # Convert e.g. -Werror=implicit-function-declaration to -Wimplicit-function-declaration.
        return '-W' + flag[8:]

    return flag


def filter_compiler_flags(compiler_flags, step, language):
    """
    This function optionaly removes flags that turn warnings into errors.
    """
    assert language in ('c', 'c++')
    assert step in ('configure', 'make')
    adjusted_flags = [
        adjust_error_on_warning_flag(flag, step, language)
        for flag in compiler_flags.split()
    ]
    return ' '.join([
        flag for flag in adjusted_flags if flag is not None
    ])


def get_path_variants(path):
    """
    Returns a list of different variants (just an absolute path vs. all symlinks resolved) for the
    given path.
    """
    return sorted(set([os.path.abspath(path), os.path.realpath(path)]))


class PostgresBuilder:
    def __init__(self):
        self.args = None
        self.build_root = None
        self.pg_build_root = None
        self.pg_prefix = None
        self.build_type = None
        self.postgres_src_dir = None
        self.compiler_type = None
        self.env_vars_for_build_stamp = set()

        # Check if the outer build is using runs the compiler on build workers.
        self.build_uses_remote_compilation = os.environ.get('YB_REMOTE_COMPILATION')
        if self.build_uses_remote_compilation == 'auto':
            raise RuntimeError(
                "No 'auto' value is allowed for YB_REMOTE_COMPILATION at this point")

    def get_yb_version(self):
        with open(os.path.join(YB_SRC_ROOT, 'version.txt'), "r") as version_file:
            return version_file.read().strip()

    def set_env_var(self, name, value):
        if value is None:
            if name in os.environ:
                del os.environ[name]
        else:
            os.environ[name] = value
        self.env_vars_for_build_stamp.add(name)

    def append_to_env_var(self, name, to_append, separator=' '):
        if name in os.environ and os.environ[name]:
            self.set_env_var(name, os.environ[name] + separator + to_append)
        else:
            self.set_env_var(name, to_append)

    def parse_args(self):
        parser = argparse.ArgumentParser(
            description='A tool for building the PostgreSQL code subtree in YugaByte DB codebase')
        parser.add_argument('--build_root',
                            default=os.environ.get('BUILD_ROOT'),
                            help='YugaByte build root directory. The PostgreSQL build/install '
                                 'directories will be created under here.')

        parser.add_argument('--run_tests',
                            action='store_true',
                            help='Run PostgreSQL tests after building it.')

        parser.add_argument('--clean',
                            action='store_true',
                            help='Clean PostgreSQL build and installation directories.')

        parser.add_argument('--cflags', help='C compiler flags')
        parser.add_argument('--cxxflags', help='C++ compiler flags')
        parser.add_argument('--ldflags', help='Linker flags for all binaries')
        parser.add_argument('--ldflags_ex', help='Linker flags for executables')
        parser.add_argument('--compiler_type', help='Compiler type, e.g. gcc or clang')

        self.args = parser.parse_args()
        if not self.args.build_root:
            raise RuntimeError("Neither BUILD_ROOT or --build-root specified")

        self.build_root = os.path.abspath(self.args.build_root)
        self.build_root_realpath = os.path.realpath(self.build_root)
        self.postgres_install_dir_include_realpath = \
            os.path.join(self.build_root_realpath, 'postgres', 'include')
        self.build_type = get_build_type_from_build_root(self.build_root)
        self.pg_build_root = os.path.join(self.build_root, 'postgres_build')
        self.build_stamp_path = os.path.join(self.pg_build_root, 'build_stamp')
        self.pg_prefix = os.path.join(self.build_root, 'postgres')
        self.build_type = get_build_type_from_build_root(self.build_root)
        self.postgres_src_dir = os.path.join(YB_SRC_ROOT, 'src', 'postgres')
        self.compiler_type = self.args.compiler_type or os.getenv('YB_COMPILER_TYPE')
        if not self.compiler_type:
            raise RuntimeError(
                "Compiler type not specified using either --compiler_type or YB_COMPILER_TYPE")

        self.export_compile_commands = os.environ.get('YB_EXPORT_COMPILE_COMMANDS') == '1'

        # This allows to skip the time-consuming compile commands file generation if it already
        # exists during debugging of this script.
        self.export_compile_commands_lazily = \
            os.environ.get('YB_EXPORT_COMPILE_COMMANDS_LAZILY') == '1'

    def adjust_cflags_in_makefile(self):
        makefile_global_path = os.path.join(self.pg_build_root, 'src/Makefile.global')
        new_makefile_lines = []
        new_cflags = os.environ['CFLAGS'].strip()
        found_cflags = False
        repalced_cflags = False
        with open(makefile_global_path) as makefile_global_input_f:
            for line in makefile_global_input_f:
                line = line.rstrip("\n")
                if line.startswith('CFLAGS = '):
                    existing_cflags = line[9:].strip()
                    found_cflags = True
                    if existing_cflags != new_cflags:
                        line = 'CFLAGS = $(YB_PREPEND_CFLAGS) ' + \
                            new_cflags + ' $(YB_APPEND_CFLAGS)'
                        replaced_cflags = True
                new_makefile_lines.append(line)

        if not found_cflags:
            raise RuntimeError("Could not find a CFLAGS line in %s" % makefile_global_path)

        if replaced_cflags:
            logging.info("Replaced cflags in %s", makefile_global_path)
            with open(makefile_global_path, 'w') as makefile_global_out_f:
                makefile_global_out_f.write("\n".join(new_makefile_lines) + "\n")

    def set_env_vars(self, step):
        if step not in ['configure', 'make']:
            raise RuntimeError(
                    ("Invalid step specified for setting env vars, must be either 'configure' "
                     "or 'make'").format(step))

        self.set_env_var('YB_PG_BUILD_STEP', step)
        self.set_env_var('YB_BUILD_ROOT', self.build_root)
        self.set_env_var('YB_SRC_ROOT', YB_SRC_ROOT)

        for var_name in ['CFLAGS', 'CXXFLAGS', 'LDFLAGS', 'LDFLAGS_EX']:
            arg_value = getattr(self.args, var_name.lower())
            self.set_env_var(var_name, arg_value)

        additional_c_cxx_flags = [
            '-Wimplicit-function-declaration',
            '-Wno-error=unused-function',
            '-DHAVE__BUILTIN_CONSTANT_P=1',
            '-DUSE_SSE42_CRC32C=1',
            '-std=c11',
            '-Werror=implicit-function-declaration',
            '-Werror=int-conversion',
        ]

        if self.compiler_type == 'clang':
            additional_c_cxx_flags += [
                '-Wno-error=builtin-requires-header'
            ]

        if step == 'make':
            additional_c_cxx_flags += [
                '-Wall',
                '-Werror',
                '-Wno-error=unused-function'
            ]

            if self.build_type == 'release':
                if self.compiler_type == 'clang':
                    additional_c_cxx_flags += [
                        '-Wno-error=array-bounds',
                        '-Wno-error=gnu-designator'
                    ]
                if self.compiler_type == 'gcc':
                    additional_c_cxx_flags += ['-Wno-error=strict-overflow']
            if self.build_type == 'asan':
                additional_c_cxx_flags += [
                    '-fsanitize-recover=signed-integer-overflow',
                    '-fsanitize-recover=shift-base',
                    '-fsanitize-recover=shift-exponent'
                ]

        # Tell gdb to pretend that we're compiling the code in the $YB_SRC_ROOT/src/postgres
        # directory.
        build_path_variants = get_path_variants(self.pg_build_root)
        src_path_variants = get_path_variants(self.postgres_src_dir)
        additional_c_cxx_flags += [
            '-fdebug-prefix-map=%s=%s' % (build_path, source_path)
            for build_path in build_path_variants
            for source_path in src_path_variants
        ]

        if self.compiler_type == 'gcc':
            additional_c_cxx_flags.append('-Wno-error=maybe-uninitialized')

        for var_name in ['CFLAGS', 'CXXFLAGS']:
            os.environ[var_name] = filter_compiler_flags(
                    os.environ.get(var_name, '') + ' ' + ' '.join(additional_c_cxx_flags),
                    step,
                    language='c' if var_name == 'CFLAGS' else 'c++')
        if step == 'make':
            self.adjust_cflags_in_makefile()

        for env_var_name in ['MAKEFLAGS']:
            if env_var_name in os.environ:
                del os.environ[env_var_name]

        compiler_wrappers_dir = os.path.join(YB_SRC_ROOT, 'build-support', 'compiler-wrappers')
        self.set_env_var('CC', os.path.join(compiler_wrappers_dir, 'cc'))
        self.set_env_var('CXX', os.path.join(compiler_wrappers_dir, 'c++'))

        self.set_env_var('LDFLAGS', re.sub(r'-Wl,--no-undefined', ' ', os.environ['LDFLAGS']))
        self.set_env_var(
            'LDFLAGS',
            re.sub(r'-Wl,--no-allow-shlib-undefined', ' ', os.environ['LDFLAGS']))

        self.append_to_env_var(
            'LDFLAGS',
            '-Wl,-rpath,' + os.path.join(self.build_root, 'lib'))

        for ldflags_var_name in ['LDFLAGS', 'LDFLAGS_EX']:
            self.append_to_env_var(ldflags_var_name, '-lm')

        if is_verbose_mode():
            # CPPFLAGS are C preprocessor flags, CXXFLAGS are C++ flags.
            for env_var_name in ['CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LDFLAGS_EX', 'LIBS']:
                if env_var_name in os.environ:
                    logging.info("%s: %s", env_var_name, os.environ[env_var_name])
        # PostgreSQL builds pretty fast, and we don't want to use our remote compilation over SSH
        # for it as it might have issues with parallelism.
        self.remote_compilation_allowed = ALLOW_REMOTE_COMPILATION and step == 'make'

        self.set_env_var(
            'YB_REMOTE_COMPILATION',
            '1' if self.remote_compilation_allowed and self.build_uses_remote_compilation else '0'
        )

        self.set_env_var('YB_BUILD_TYPE', self.build_type)

        # Do not try to make rpaths relative during the configure step, as that may slow it down.
        self.set_env_var('YB_DISABLE_RELATIVE_RPATH', '1' if step == 'configure' else '0')
        if self.build_type == 'compilecmds':
            os.environ['YB_SKIP_LINKING'] = '1'

        # This could be set to False to skip build and just perform additional tasks such as
        # exporting the compilation database.
        self.should_build = True

    def sync_postgres_source(self):
        logging.info("Syncing postgres source code")
        # Remove source code files from the build directory that have been removed from the
        # source directory.
        # TODO: extend this to the complete list of source file types.
        run_program([
            'rsync', '-avz',
            '--include', '*.c',
            '--include', '*.h',
            '--include', 'Makefile',
            '--include', '*.am',
            '--include', '*.in',
            '--include', '*.mk',
            '--exclude', '*',
            '--delete',
            self.postgres_src_dir + '/', self.pg_build_root],
            capture_output=False)
        run_program([
            'rsync',
            '-az',
            '--exclude', '*.sw?',
            '--exclude', '*.bak',
            '--exclude', '*.orig',
            '--exclude', '*.rej',
            self.postgres_src_dir + '/', self.pg_build_root],
            capture_output=False)
        if is_verbose_mode():
            logging.info("Successfully synced postgres source code")

    def clean_postgres(self):
        logging.info("Removing the postgres build and installation directories")
        for dir_to_delete in [self.pg_build_root, self.pg_prefix]:
            logging.info("Deleting the directory '%s'", dir_to_delete)
            # rm -rf is much faster than Python's rmtree.
            run_program(['rm', '-rf', dir_to_delete], capture_output=False)

    def configure_postgres(self):
        if is_verbose_mode():
            logging.info("Running configure in the postgres build directory")
        # Don't enable -Werror when running configure -- that can affect the resulting
        # configuration.
        configure_cmd_line = [
                './configure',
                '--prefix', self.pg_prefix,
                '--with-extra-version=-YB-' + self.get_yb_version(),
                '--enable-depend',
                # We're enabling debug symbols for all types of builds.
                '--enable-debug']
        if not get_bool_env_var('YB_NO_PG_CONFIG_CACHE'):
            configure_cmd_line.append('--config-cache')

        # We get readline-related errors in ASAN/TSAN, so let's disable readline there.
        if self.build_type in ['asan', 'tsan']:
            # TODO: do we still need this limitation?
            configure_cmd_line += ['--without-readline']

        if self.build_type != 'release':
            configure_cmd_line += ['--enable-cassert']
        configure_result = run_program(configure_cmd_line, error_ok=True)
        if configure_result.failure():
            rerun_configure = False
            for line in configure_result.stderr.splitlines():
                if REMOVE_CONFIG_CACHE_MSG_RE.search(line.strip()):
                    logging.info("Configure failed because of stale config.cache, re-running.")
                    run_program('rm -f config.cache')
                    rerun_configure = True
                    break

            if not rerun_configure:
                logging.error("Standard error from configure:\n" + configure_result.stderr)
                raise RuntimeError("configure failed")

            configure_result = run_program(
                configure_cmd_line, shell=True, stdout_stderr_prefix='configure', error_ok=True)

        if is_verbose_mode() and configure_result.success():
            configure_result.print_output_to_stdout()

        configure_result.print_output_and_raise_error_if_failed()

        logging.info("Successfully ran configure in the postgres build directory")

    def get_env_vars_str(self, env_var_names):
        return "\n".join(
            "%s=%s" % (k, os.environ[k])
            for k in sorted(env_var_names)
            if k in os.environ
        )

    def get_build_stamp(self, include_env_vars):
        """
        Creates a "build stamp" that tries to capture all inputs that might affect the PostgreSQL
        code. This is needed to avoid needlessly rebuilding PostgreSQL, as it takes ~10 seconds
        even if there are no code changes.
        """

        with WorkDirContext(YB_SRC_ROOT):
            code_subset = [
                'src/postgres',
                'src/yb/yql/pggate',
                'python/yb/build_postgres.py',
                'build-support/build_postgres',
                'CMakeLists.txt'
            ]
            git_hash = subprocess.check_output(
                ['git', '--no-pager', 'log', '-n', '1', '--pretty=%H'] + code_subset).strip()
            git_diff = subprocess.check_output(['git', 'diff'] + code_subset)
            git_diff_cached = subprocess.check_output(['git', 'diff', '--cached'] + code_subset)

        env_vars_str = self.get_env_vars_str(self.env_vars_for_build_stamp)
        build_stamp = "\n".join([
            "git_commit_sha1=%s" % git_hash,
            "git_diff_sha256=%s" % sha256(git_diff),
            "git_diff_cached_sha256=%s" % sha256(git_diff_cached)
            ])

        if include_env_vars:
            build_stamp += "\nenv_vars_sha256=%s" % hashlib.sha256(env_vars_str).hexdigest()

        return build_stamp.strip()

    def get_saved_build_stamp(self):
        if os.path.exists(self.build_stamp_path):
            with open(self.build_stamp_path) as build_stamp_file:
                return build_stamp_file.read().strip()

    def make_postgres(self):
        self.set_env_vars('make')
        # Postgresql requires MAKELEVEL to be 0 or non-set when calling its make.
        # But in case YB project is built with make, MAKELEVEL is not 0 at this point.
        make_cmd = ['make', 'MAKELEVEL=0']

        make_parallelism = os.environ.get('YB_MAKE_PARALLELISM')
        if make_parallelism:
            make_parallelism = int(make_parallelism)
        if self.build_uses_remote_compilation and not self.remote_compilation_allowed:
            # Since we're building everything locally in this case, and YB_MAKE_PARALLELISM is
            # likely specified for distributed compilation, cap it at some factor times the number
            # of CPU cores.
            parallelism_cap = multiprocessing.cpu_count() * 2
            if make_parallelism:
                make_parallelism = min(parallelism_cap, make_parallelism)
            else:
                make_parallelism = cpu_count

        if make_parallelism:
            make_cmd += ['-j', str(int(make_parallelism))]

        self.set_env_var('YB_COMPILER_TYPE', self.compiler_type)

        # Create a script allowing to easily run "make" from the build directory with the right
        # environment.
        env_script_content = ''
        for env_var_name in CONFIG_ENV_VARS:
            env_var_value = os.environ.get(env_var_name)
            if env_var_value is None:
                raise RuntimeError("Expected env var %s to be set" % env_var_name)
            env_script_content += "export %s=%s\n" % (env_var_name, quote_for_bash(env_var_value))

        compile_commands_files = []

        work_dirs = [self.pg_build_root]
        if self.build_type != 'compilecmds':
            work_dirs.append(os.path.join(self.pg_build_root, 'contrib'))

        for work_dir in work_dirs:
            with WorkDirContext(work_dir):
                # Create a script to run Make easily with the right environment.
                if self.should_build:
                    make_script_path = 'make.sh'
                    with open(make_script_path, 'w') as out_f:
                        out_f.write(
                            '#!/usr/bin/env bash\n'
                            '. "${BASH_SOURCE%/*}"/env.sh\n'
                            'make "$@"\n')
                    with open('env.sh', 'w') as out_f:
                        out_f.write(env_script_content)

                    run_program(['chmod', 'u+x', make_script_path])

                    # Actually run Make.
                    if is_verbose_mode():
                        logging.info("Running make in the %s directory", work_dir)
                    run_program(
                        make_cmd, stdout_stderr_prefix='make', cwd=work_dir, shell=True,
                        error_ok=True).print_output_and_raise_error_if_failed()
                if self.build_type == 'compilecmds':
                    logging.info(
                            "Not running make install in the %s directory since we are only "
                            "generating the compilation database", work_dir)
                else:
                    run_program(
                            'make install', stdout_stderr_prefix='make_install',
                            cwd=work_dir, shell=True, error_ok=True
                        ).print_output_and_raise_error_if_failed()
                    logging.info("Successfully ran make in the %s directory", work_dir)

                if self.export_compile_commands:
                    logging.info("Generating the compilation database in directory '%s'", work_dir)

                    compile_commands_path = os.path.join(work_dir, 'compile_commands.json')
                    self.set_env_var('YB_PG_SKIP_CONFIG_STATUS', '1')
                    if (not os.path.exists(compile_commands_path) or
                            not self.export_compile_commands_lazily):
                        run_program(['compiledb', 'make', '-n'], capture_output=False)
                    del os.environ['YB_PG_SKIP_CONFIG_STATUS']

                    if not os.path.exists(compile_commands_path):
                        raise RuntimeError("Failed to generate compilation database at: %s" %
                                           compile_commands_path)
                    compile_commands_files.append(compile_commands_path)

        if self.export_compile_commands:
            self.combine_compile_commands(compile_commands_files)

    def combine_compile_commands(self, compile_commands_files):
        """
        Combine compilation commands files from main and contrib subtrees of PostgreSQL, patch them
        so that they point to the original source directory, and concatenate with the main
        compilation commands file.
        """
        new_compile_commands = []
        for compile_commands_path in (
                compile_commands_files + [
                    os.path.join(self.build_root, 'compile_commands.json')
                ]):
            with open(compile_commands_path) as compile_commands_file:
                new_compile_commands += json.load(compile_commands_file)

        new_compile_commands = [
            self.postprocess_pg_compile_command(item)
            for item in new_compile_commands
        ]

        output_path = os.path.join(self.build_root, 'combined_compile_commands.json')
        with open(output_path, 'w') as compile_commands_output_file:
            json.dump(new_compile_commands, compile_commands_output_file, indent=2)
        logging.info("Wrote the compilation commands file to: %s", output_path)

        dest_link_path = os.path.join(YB_SRC_ROOT, 'compile_commands.json')
        if (not os.path.exists(dest_link_path) or
                os.path.realpath(dest_link_path) != os.path.realpath(output_path)):
            if os.path.exists(dest_link_path):
                logging.info("Removing the old file/link at %s", dest_link_path)
                os.remove(dest_link_path)
            os.symlink(
                os.path.relpath(
                    os.path.realpath(output_path),
                    os.path.realpath(YB_SRC_ROOT)),
                dest_link_path)
            logging.info("Created symlink at %s", dest_link_path)

    def postprocess_pg_compile_command(self, compile_command_item):
        directory = compile_command_item['directory']
        if 'command' not in compile_command_item and 'arguments' not in compile_command_item:
            raise ValueError(
                "Invalid compile command item: %s (neither 'command' nor 'arguments' are present)" %
                json.dumps(compile_command_item))
        if 'command' in compile_command_item and 'arguments' in compile_command_item:
            raise ValueError(
                "Invalid compile command item: %s (both 'command' and 'arguments' are present)" %
                json.dumps(compile_command_item))

        file_path = compile_command_item['file']

        new_directory = directory
        if directory.startswith(self.pg_build_root + '/'):
            new_directory = os.path.join(YB_SRC_ROOT, 'src', 'postgres',
                                         os.path.relpath(directory, self.pg_build_root))
            # Some files only exist in the postgres build directory. We don't switch the work
            # directory of the compiler to the original source directory in those cases.
            if (not os.path.isabs(file_path) and
                    not os.path.isfile(os.path.join(new_directory, file_path))):
                new_directory = directory

        if 'arguments' in compile_command_item:
            assert 'command' not in compile_command_item
            arguments = compile_command_item['arguments']
        else:
            assert 'arguments' not in compile_command_item
            arguments = compile_command_item['command'].split()

        new_args = []
        already_added_include_paths = set()
        original_include_paths = []
        additional_postgres_include_paths = []

        def add_original_include_path(original_include_path):
            if (original_include_path not in already_added_include_paths and
                    original_include_path not in original_include_paths):
                original_include_paths.append(original_include_path)

        def handle_original_include_path(include_path):
            if (os.path.realpath(include_path) == self.postgres_install_dir_include_realpath and
                    not additional_postgres_include_paths):
                additional_postgres_include_paths.extend([
                    os.path.join(YB_SRC_ROOT, 'src', 'postgres', 'src', 'interfaces', 'libpq'),
                    os.path.join(
                        self.build_root_realpath, 'postgres_build', 'src', 'include'),
                    os.path.join(
                        self.build_root_realpath, 'postgres_build', 'interfaces', 'libpq')
                ])

        for arg in arguments:
            added = False
            if arg.startswith('-I'):
                include_path = arg[2:]
                if os.path.isabs(include_path):
                    # This is already an absolute path, append it as is.
                    new_args.append(arg)
                    handle_original_include_path(arg)
                else:
                    original_include_path = os.path.realpath(os.path.join(directory, include_path))
                    # This is a relative path. Try to rewrite it relative to the new directory
                    # where we are running the compiler.
                    new_include_path = os.path.join(new_directory, include_path)
                    if os.path.isdir(new_include_path):
                        new_args.append('-I' + new_include_path)
                        # This is to avoid adding duplicate paths.
                        already_added_include_paths.add(new_include_path)
                        already_added_include_paths.add(os.path.abspath(new_include_path))
                    else:
                        # Append the path as is -- maybe the directory will get created later.
                        new_args.append(arg)

                    # In any case, for relative paths, add the absolute path in the original
                    # directory at the end of the compiler command line.
                    add_original_include_path(original_include_path)
                    handle_original_include_path(original_include_path)

            else:
                new_args.append(arg)

            # Replace the compiler path in compile_commands.json according to user preferences.
            compiler_basename = os.path.basename(new_args[0])
            new_compiler_path = None
            if compiler_basename in ['cc', 'gcc', 'clang']:
                new_compiler_path = os.environ.get('YB_CC_FOR_COMPILE_COMMANDS')
            elif compiler_basename in ['c++', 'g++', 'clang++']:
                new_compiler_path = os.environ.get('YB_CXX_FOR_COMPILE_COMMANDS')
            else:
                logging.warning(
                    "Unexpected compiler path: %s. Compile command: %s",
                    new_args[0], json.dumps(compile_command_item))
            if new_compiler_path:
                new_args[0] = new_compiler_path

        new_args += [
            '-I%s' % include_path
            for include_path in additional_postgres_include_paths + original_include_paths]

        new_file_path = file_path
        if not os.path.isabs(file_path):
            new_file_path = os.path.join(new_directory, file_path)
            if not os.path.isfile(new_file_path):
                new_file_path = file_path

        return {
            'directory': new_directory,
            'file': new_file_path,
            'arguments': new_args
        }

    def run(self):
        if get_bool_env_var('YB_SKIP_POSTGRES_BUILD'):
            logging.info("Skipping PostgreSQL build (YB_SKIP_POSTGRES_BUILD is set)")
            return

        self.parse_args()
        self.build_postgres()

    def build_postgres(self):
        if self.args.clean:
            self.clean_postgres()

        mkdir_p(self.pg_build_root)

        self.set_env_vars('configure')
        saved_build_stamp = self.get_saved_build_stamp()
        initial_build_stamp = self.get_build_stamp(include_env_vars=True)
        initial_build_stamp_no_env = self.get_build_stamp(include_env_vars=False)
        logging.info("PostgreSQL build stamp:\n%s", initial_build_stamp)
        if initial_build_stamp == saved_build_stamp:
            logging.info(
                "PostgreSQL is already up-to-date in directory %s, not rebuilding.",
                self.pg_build_root)
            if self.export_compile_commands:
                self.should_build = False
                logging.info("Still need to create compile_commands.json, proceeding.")
            else:
                return
        with WorkDirContext(self.pg_build_root):
            if self.should_build:
                self.sync_postgres_source()
                if os.environ.get('YB_PG_SKIP_CONFIGURE', '0') != '1':
                    self.configure_postgres()
            self.make_postgres()

        final_build_stamp_no_env = self.get_build_stamp(include_env_vars=False)
        if final_build_stamp_no_env == initial_build_stamp_no_env:
            logging.info("Updating build stamp file at %s", self.build_stamp_path)
            with open(self.build_stamp_path, 'w') as build_stamp_file:
                build_stamp_file.write(initial_build_stamp)
        else:
            logging.warning("PostgreSQL build stamp changed during the build! Not updating.")


if __name__ == '__main__':
    init_logging()
    PostgresBuilder().run()
