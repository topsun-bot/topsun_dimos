# Security Policy

## About this repository

[topsun-bot/topsun_dimos](https://github.com/topsun-bot/topsun_dimos)
is a public fork of
[dimensionalOS/dimos](https://github.com/dimensionalOS/dimos).
This file is the security policy for **this fork**. It does not speak
for upstream Dimensional / DimOS.

## Reporting a vulnerability

Please report suspected security vulnerabilities **privately**. Do not
open a public GitHub issue, discussion, or pull request for:

- exploitable vulnerabilities
- leaked credentials, tokens, keys, or other secrets
- other reports that could help an attacker if disclosed early

### This repository

Use GitHub Security Advisories / private vulnerability reporting on
this repo:

**[Report a vulnerability on topsun-bot/topsun_dimos](https://github.com/topsun-bot/topsun_dimos/security/advisories/new)**

If the private reporting form is not available, do not fall back to a
public issue. Wait until private reporting is enabled, or contact a
repository maintainer through a private channel.

Include enough detail to reproduce the issue (affected branch or
commit, component, steps, and impact). Do not include live secrets in
the report; describe what leaked and where.

This fork does **not** publish a dedicated security email, bug bounty,
or response SLA. Maintainers will review reports as capacity allows.

### Leaked secrets

If you discovered credentials or other secrets in this repository or
its history:

1. Assume they are compromised. **Rotate or revoke them immediately**
   (invalidate the old value; issue a new one if still needed).
2. Report the leak privately using the advisory form above.
3. Do not paste the secret into a public issue, comment, or pull
   request.

### Upstream DimOS

Vulnerabilities in shared DimOS code may also affect
[dimensionalOS/dimos](https://github.com/dimensionalOS/dimos).

As of this writing, upstream **does not** publish a `SECURITY.md` or a
dedicated security contact in its README or docs
([no security policy detected](https://github.com/dimensionalOS/dimos/security/policy)).
Please also follow whatever process upstream documents if that
changes, and report to upstream as well when the issue is not
fork-specific:

- Check the [dimensionalOS/dimos security policy](https://github.com/dimensionalOS/dimos/security/policy)
- Use [upstream private vulnerability reporting](https://github.com/dimensionalOS/dimos/security/advisories/new)
  if it is enabled

Do not assume a bounty, SLA, or email contact exists for either
repository unless they publish one.

## Supported versions

This fork is pre-release software. Reports are accepted against the
default branch (`main`). There is no separately supported release
train or backport commitment.

## Automated controls already enabled

The following GitHub features are enabled on this repository. They
reduce some classes of risk; they do **not** find every vulnerability,
stop every secret leak, or replace review of a report.

| Control | Role |
| --- | --- |
| [CodeQL](https://github.com/topsun-bot/topsun_dimos/security/code-scanning) | Static analysis for a subset of vulnerability classes |
| Secret scanning and push protection | Detects and can block some known secret patterns on push |
| [Dependabot](https://github.com/topsun-bot/topsun_dimos/security/dependabot) | Dependency update PRs and known-advisory alerts |

A clean scan is not a security guarantee.

## Non-security reports

Bugs that are not security vulnerabilities, feature requests, and
hardware or operational safety questions belong in the normal issue or
discussion process described in [CONTRIBUTING.md](CONTRIBUTING.md).
Physical robot safety (for example, not shipping untested motion) is
important and is separate from this vulnerability-reporting process.
