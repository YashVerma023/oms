# OMP documentation

Read in order if you are new to the codebase; each document assumes the ones
before it.

| # | Document | Read it when |
|---|---|---|
| 01 | [Setup](01-setup.md) | Getting the app running locally |
| 02 | [Architecture](02-architecture.md) | Understanding module boundaries before changing anything |
| 03 | [Database](03-database.md) | Touching schema, columns or queries |
| 04 | [Data import](04-data-import.md) | Changing how sheets are parsed or loaded |
| 05 | [Business rules](05-business-rules.md) | Changing ml_pct or the running-state linkage |
| 06 | [Auth and roles](06-auth-and-roles.md) | Adding a role or a permission-gated page |
| 07 | [Frontend](07-frontend.md) | Adding a page, a tab or styling |
| 08 | [Operations](08-operations.md) | Deploying, debugging, or assessing risk |

## Conventions used across these docs

- Paths are relative to the project root.
- SQL identifiers are shown backticked, as the code writes them, because
  several column names contain spaces or start with a digit.
- "Sheet" means an uploaded CSV or Excel worksheet; "table" means MySQL.
