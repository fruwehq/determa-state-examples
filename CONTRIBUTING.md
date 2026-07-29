# Contributing

## Workflow

Open one issue for one focused change, create one branch, and open one draft pull
request. Pull requests are squash-merged after checks pass and all review
conversations are resolved. Assign the pull request to `fruwe`.

Do not include assistant attribution in commits, pull requests, comments, or
documentation.

## Proposing an example

An example proposal must describe:

- the real-world workflow and why it is meaningful;
- the intended audience, language, framework, and integration style;
- the external systems or effects represented;
- the failure, restart, maintenance, and debugging behavior it will demonstrate;
- how a reviewer can run and test it from its folder alone.

An isolated syntax or feature demonstration belongs in the
[manual and tutorials](https://github.com/fruwehq/determa-state-docs), not here.

## Required folder contents

Each example must be a complete top-level folder that owns:

- `README.md` with purpose, architecture, prerequisites, start, reset, maintenance,
  and debugging instructions;
- language-specific dependency manifest and committed lockfile;
- Determa State machine definitions;
- application source code;
- automated tests and commands to run them;
- a Dockerfile, Compose file, database setup, or other local infrastructure
  when the workflow requires it.

The example must pin released dependencies and must run, build, and test
without reading code, configuration, dependencies, or generated artifacts from
the repository root or another example. References to root documentation are
allowed, but they must not be required to operate the application.

Do not add a shared root dependency manifest or shared application library.
Root CI may orchestrate the commands documented by each example, but it must
not supply hidden runtime or build requirements.

## Integration styles

Direct-library examples may use a released Python, Rust, or future implementation.
Language-neutral process, command-line, or socket examples must wait for an actual
supported execution boundary and must document the precise released interface they
use. Do not emulate or promise unsupported execution CLI or socket behavior.

## Validation

Before requesting review:

1. Follow the example README from a clean checkout and from the example folder.
2. Run its tests and formatting or linting checks.
3. Verify reset and debugging instructions.
4. Check Markdown links and formatting.
5. Confirm the diff contains no root dependency manifest or cross-example dependency.
