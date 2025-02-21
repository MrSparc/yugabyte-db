// Copyright (c) YugaByte, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
// in compliance with the License.  You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software distributed under the License
// is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
// or implied.  See the License for the specific language governing permissions and limitations
// under the License.
//

#ifndef YB_CLIENT_YB_TABLE_NAME_H_
#define YB_CLIENT_YB_TABLE_NAME_H_

#include <string>

#ifdef YB_HEADERS_NO_STUBS
#include "yb/util/logging.h"
#else
#include "yb/client/stubs.h"
#endif

#include "yb/common/redis_constants_common.h"

namespace yb {

namespace master {
class NamespaceIdentifierPB;
class TableIdentifierPB;
}

namespace client {

// Is system keyspace read-only?
DECLARE_bool(yb_system_namespace_readonly);

// The class is used to store a table name, which can include namespace name as a suffix.
class YBTableName {
 public:
  // Empty (undefined) name.
  YBTableName() {}

  // Complex table name: 'namespace_name.table_name'.
  // The namespace must not be empty.
  // For the case of undefined namespace the next constructor must be used.
  YBTableName(const std::string& namespace_name, const std::string& table_name) {
    set_namespace_name(namespace_name);
    set_table_name(table_name);
  }

  YBTableName(const std::string& namespace_id, const std::string& namespace_name,
              const std::string& table_name) {
    set_namespace_id(namespace_id);
    set_namespace_name(namespace_name);
    set_table_name(table_name);
  }

  YBTableName(const std::string& namespace_id, const std::string& namespace_name,
              const std::string& table_id, const std::string& table_name) {
    set_namespace_id(namespace_id);
    set_namespace_name(namespace_name);
    set_table_id(table_id);
    set_table_name(table_name);
  }

  // Simple table name (no namespace provided at the moment of construction).
  // In this case the namespace has not been set yet and it MUST be set later.
  explicit YBTableName(const std::string& table_name) {
    set_table_name(table_name);
  }

  bool empty() const {
    return namespace_id_.empty() && namespace_name_.empty() && table_name_.empty();
  }

  bool has_namespace() const {
    return !namespace_name_.empty();
  }

  const std::string& namespace_name() const {
    return namespace_name_; // Can be empty.
  }

  const std::string& namespace_id() const {
    return namespace_id_; // Can be empty.
  }

  const std::string& resolved_namespace_name() const {
    DCHECK(has_namespace()); // At the moment the namespace name must NEVER be empty.
                             // It must be set by set_namespace_name() before this call.
                             // If the check fails - you forgot to call set_namespace_name().
    return namespace_name_;
  }

  bool has_table() const {
    return !table_name_.empty();
  }

  const std::string& table_name() const {
    return table_name_;
  }

  bool has_table_id() const {
    return !table_id_.empty();
  }

  const std::string& table_id() const {
    return table_id_; // Can be empty
  }

  bool is_system() const;

  bool is_redis_namespace() const {
    return ((has_namespace() && resolved_namespace_name() == common::kRedisKeyspaceName));
  }

  bool is_redis_table() const {
    return (
        (has_namespace() && resolved_namespace_name() == common::kRedisKeyspaceName) &&
        table_name_.find(common::kRedisTableName) == 0);
  }

  std::string ToString() const {
    return (has_namespace() ? namespace_name_ + '.' + table_name_ : table_name_);
  }

  void set_namespace_id(const std::string& namespace_id) {
    DCHECK(!namespace_id.empty());
    namespace_id_ = namespace_id;
  }

  void set_namespace_name(const std::string& namespace_name) {
    DCHECK(!namespace_name.empty());
    namespace_name_ = namespace_name;
  }

  void set_table_name(const std::string& table_name) {
    DCHECK(!table_name.empty());
    table_name_ = table_name;
  }

  void set_table_id(const std::string& table_id) {
    DCHECK(!table_id.empty());
    table_id_ = table_id;
  }

  // ProtoBuf helpers.
  void SetIntoTableIdentifierPB(master::TableIdentifierPB* id) const;
  void GetFromTableIdentifierPB(const master::TableIdentifierPB& id);

  void SetIntoNamespaceIdentifierPB(master::NamespaceIdentifierPB* id) const;
  void GetFromNamespaceIdentifierPB(const master::NamespaceIdentifierPB& id);

 private:
  std::string namespace_id_; // Optional. Can be set when the client knows the namespace id also.
  std::string namespace_name_; // Can be empty, that means the namespace has not been set yet.
  std::string table_id_; // Optional. Can be set when client knows the table id also.
  std::string table_name_;
};

inline bool operator ==(const YBTableName& lhs, const YBTableName& rhs) {
  // Not comparing namespace_id and table_id because they are optional.
  return (lhs.namespace_name() == rhs.namespace_name() && lhs.table_name() == rhs.table_name());
}

inline bool operator !=(const YBTableName& lhs, const YBTableName& rhs) {
  return !(lhs == rhs);
}

// In order to be able to use YBTableName with boost::hash
size_t hash_value(const YBTableName& table_name);

}  // namespace client
}  // namespace yb

#endif  // YB_CLIENT_YB_TABLE_NAME_H_
