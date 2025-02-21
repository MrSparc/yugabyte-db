## 1. Create roles

Create a role with a password. You can do this with the [CREATE ROLE](../../api/YSQL/ddl_create_role/) command.

As an example, let us create a role `engineering` for an engineering team in an organization.

```postgresql
yugabyte=# CREATE ROLE engineering;
```

Roles that have `LOGIN` privileges are users. As an example, you can create a user `john` as follows:

```postgresql
yugabyte=# CREATE ROLE john LOGIN PASSWORD 'PasswdForJohn';
```

Read about [how to create users in YugabyteDB](../../ysql-authentication/) in the authentication section.

## 2. Grant roles

You can grant a role to another role (which can be a user), or revoke a role that has already been granted. Executing the `GRANT` and the `REVOKE` operations requires the `AUTHORIZE` permission on the role being granted or revoked.

As an example, you can grant the `engineering` role we created above to the user `john` as follows:

```postgresql
yugabyte=# GRANT engineering TO john;
```

Read more about [granting roles](../../api/YSQL/dcl_grant/).

## 3. Create a hierarchy of roles, if needed

In YSQL, you can create a hierarchy of roles. The permissions of any role in the hierarchy flows downward.

As an example, let us say that in the above example, we want to create a `developer` role that inherits all the permissions from the `engineering` role. You can achieve this as follows.

First, create the `developer` role.

```postgresql
yugabyte=# CREATE ROLE developer;
```

Next, `GRANT` the `engineering` role to the `developer` role.

```postgresql
yugabyte=# GRANT engineering TO developer;
```

## 4. List roles

You can list all the roles by running the following command:

```postgresql
yugabyte=# SELECT rolname, rolcanlogin, rolsuper, memberof FROM pg_roles;
```

You should see the following output:

```
 rolname     | rolcanlogin | rolsuper | memberof
-------------+-------------+----------+-----------------
 john        | t           | f        | {engineering}
 developer   | f           | f        | {engineering}
 engineering | f           | f        | {}
 yugabyte    | t           | t        | {}

(4 rows)
```

In the table above, note the following:

* The `yugabyte` role is the built-in superuser.
* The role `john` can login, and hence is a user. Note that `john` is not a superuser.
* The roles `engineering` and `developer` cannot login.
* Both `john` and `developer` inherit the role `engineering`.

## 5. Revoke roles

Roles can be revoked using the [REVOKE](../../api/YSQL/ddl_revoke/) command.

In the above example, we can revoke the `engineering` role from the user `john` as follows:

```postgresql
yugabyte=# REVOKE engineering FROM john;
```

Listing all the roles now shows that `john` no longer inherits from the `engineering` role:

```postgresql
yugabyte=# SELECT rolname, rolcanlogin, rolsuperuser, memberof FROM pg_roles;
```

```
 rolname     | rolcanlogin | rolsuper | memberof
-------------+-------------+----------+-----------------
john         | t           | f        | {}
developer    | f           | f        | {engineering}
engineering  | f           | f        | {}
yugabyte     | t           | t        | {}

(4 rows)
```

## 6. Drop roles

Roles can be dropped with the [DROP ROLE](../../api/YSQL/ddl_drop_role/) command.

In the above example, we can drop the `developer` role with the following command:

```postgresql
yugabyte=# DROP ROLE developer;
```

The `developer` role would no longer be present upon listing all the roles:

```postgresql
yugabyte=# SELECT rolname, rolcanlogin, rolsuper, memberof FROM pg_roles;
```

```
 rolname     | rolcanlogin | rolsuper | memberof
-------------+-------------+----------+-----------
 john        | t           | f        | {}
 engineering | f           | f        | {}
 yugabyte    | t           | t        | {}

(3 rows)
```
