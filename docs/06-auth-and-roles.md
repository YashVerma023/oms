# 06 — Auth and roles

`auth.py`. Plain Flask sessions — no Flask-Login. About 30 lines of decorators
instead of a dependency.

---

## Roles

| Role | Status | Access |
|---|---|---|
| `superadmin` | implemented | Everything admin has, plus MSUsers |
| `admin` | implemented | Dashboard, all table tabs, edit, uploads |
| `data` | login only | No views yet |
| `operator` | login only | No views yet |
| `crm` | login only | No views yet |

The canonical list is `core/logins.py::ROLES`, used by the MSUsers form and
intended as the single source when the remaining roles are built.

Admin and superadmin deliberately share every existing view: the admin
blueprint's routes are decorated `@roles_required("admin", "superadmin")`, so
nothing is duplicated. `roles/superadmin/` contains only the extra.

---

## Login

`POST /login` with email and password.

```
lookup by LOWER(email)
compare password            # plain text, by project decision
session.clear()
session["user_id"], ["role"], ["name"], ["email"]
redirect
```

Failures log the attempted email and remote address at WARNING and return a
generic "Invalid email or password" — the message does not reveal whether the
address exists.

### Redirect safety

`?next=` is honoured only when it starts with `/` and not `//`. An absolute
URL is ignored and the role's landing page used instead, so the login page
cannot be used as an open redirect.

### Landing pages

`ROLE_HOME` maps role → endpoint. All five currently point at
`admin.dashboard`; give a role its own landing page by changing one line once
its blueprint exists.

---

## Decorators

```python
@login_required                        # redirects anonymous users to /login?next=...
@roles_required("admin", "superadmin") # implies @login_required
```

`roles_required` logs a warning naming the user, role and path, flashes a
message, and redirects to the user's own landing page. It is applied to
**every** route in both role blueprints — including the JSON endpoint
`/admin/api/table/<page_key>`, so data cannot be fetched by an unauthorised
session.

Template-level checks are cosmetic only. The MSUsers menu item is wrapped in
`{% if session.get('role') == 'superadmin' %}`, but the route is what actually
enforces access.

---

## MSUsers

`/superadmin/msusers`, superadmin only.

- Lists the full `login` table — role, name, email, password, created.
- **Add user** reveals a form for role (dropdown), name, email, password.

Validation rejects blank fields, unknown roles, malformed email, and duplicate
email (checked case-insensitively before insert; the column is also `UNIQUE`).
On failure the form stays open with the input intact.

There is no edit or delete yet — add them in `core/logins.py` alongside
`create_login`.

---

## Bootstrap logins

Seeded on startup by `ensure_default_admin()`, only when the email is absent:

| Role | Email | Password | Env prefix |
|---|---|---|---|
| admin | `admin@gmail.com` | `admin123` | `DEFAULT_ADMIN_` |
| superadmin | `superadmin@gmail.com` | `sadmin123` | `DEFAULT_SUPERADMIN_` |

Because the check is by email, changing a password through the portal is not
overwritten on the next restart.

---

## Adding a role

1. `roles/<role>/__init__.py`:

   ```python
   bp = Blueprint("<role>", __name__, url_prefix="/<role>")

   @bp.route("/")
   @roles_required("<role>", "superadmin")
   def dashboard():
       return render_template("<role>/dashboard.html")
   ```

2. Register it in `app.py`.
3. Point `ROLE_HOME["<role>"]` at the new endpoint.
4. Add `templates/<role>/dashboard.html` extending `base.html`.
5. Add the role to `core/logins.py::ROLES` if it is new.

Reuse `templates/shared/table.html` and `core/tables.py` for any grid the role
needs — restrict its columns with the `visible` whitelist rather than building
a second grid.

---

## Security posture

Known and accepted for an internal, isolated deployment:

| Item | Status |
|---|---|
| Password storage | **Plain text**, by decision, so superadmin can read them |
| CSRF protection | **Not implemented** — no tokens on any form |
| Session cookie flags | Flask defaults; set `SESSION_COOKIE_SECURE` behind HTTPS |
| Rate limiting | None — login accepts unlimited attempts |
| SQL injection | Values parameterised; identifiers whitelisted or from `information_schema` |
| Open redirect | Prevented on `?next=` |
| Upload size | Capped by `MAX_UPLOAD_MB` before the body is read |

Before this is reachable beyond the internal network, address password hashing
and CSRF first. See [08-operations.md](08-operations.md).
