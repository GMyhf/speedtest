# Repository Guidelines

## Project Structure & Module Organization

This repository contains a standalone China Mobile broadband diagnostic tool. The main program is `cmcc_broadband_diag.py`, which includes CLI parsing, network checks, report rendering, and built-in self-tests in one standard-library Python file. `README.md` documents user-facing usage in Chinese. Runtime output is written to `reports/` by default; treat that directory as generated evidence, not source code.

## Build, Test, and Development Commands

- `python3 cmcc_broadband_diag.py --self-test`: runs internal checks that do not access the network.
- `python3 cmcc_broadband_diag.py --app-group-fault`: runs the normal diagnostic flow and writes text/JSON reports to `reports/`.
- `python3 cmcc_broadband_diag.py --app-group-fault --deep`: adds traceroute/tracepath checks; expect slower execution.
- `python3 cmcc_broadband_diag.py --speedtest`: runs speed testing where supported, using macOS `networkQuality` when available.
- `python3 cmcc_broadband_diag.py --no-write --json`: useful during development to inspect structured output without creating report files.

There is no packaging or build step at present.

## Coding Style & Naming Conventions

Use Python 3 and the standard library unless a dependency is clearly justified. Follow the existing style: 4-space indentation, type hints for function signatures, `snake_case` names, uppercase constants, and `dataclasses` for structured results. Keep user-facing diagnostic text concise and in Chinese to match current output. Prefer explicit command argument lists with `subprocess.run` over shell strings.

## Testing Guidelines

Run `python3 cmcc_broadband_diag.py --self-test` before committing changes. For changes touching network behavior, also run a non-writing command such as `python3 cmcc_broadband_diag.py --app-group-fault --no-write` from a normal terminal, since sandboxed environments can block ICMP, DNS, or routing probes. If adding tests, keep them network-independent unless they are clearly documented as manual integration checks.

## Commit & Pull Request Guidelines

Existing commits use short imperative messages, for example `Add broadband diagnostic reports`. Keep commit subjects concise and action-oriented. Pull requests should describe the scenario being improved, list tested commands, and note any platform-specific behavior on Linux, macOS, or Windows. Include sample report snippets or screenshots only when output formatting changes.

## Security & Configuration Tips

Do not commit generated reports if they contain personal network details, account clues, public IPs, or local gateway information. The script should remain read-only with respect to system, router, and optical modem settings.
