# Security Policy

## Supported Versions

Only the latest release is supported. Please update to the latest version before reporting an issue.

## Reporting a Vulnerability

This integration reads data from its official Austrian data source over HTTPS using the personal API key you registered — the key is stored only in your Home Assistant configuration and sent only to api.e-control.at. It handles no other credentials, no personal data, and has no write access to any external system. If you still believe you have found a security issue (e.g. in how data is parsed or how entities are exposed), please report it privately via [GitHub Security Advisories](../../security/advisories/new) rather than opening a public issue.

For anything that is not security-sensitive (bugs, feature requests), please use the regular [Issues](../../issues) tab instead.
