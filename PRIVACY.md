# Privacy Policy

Nanachi Taiwan Stock Panel is a self-hosted, open-source application.

## Local data

The application stores user-specific data locally on the user's device,
including where applicable:

- portfolio and transaction information;
- watchlists;
- alert rules;
- application preferences;
- AI provider configuration;
- FinMind configuration;
- LINE and Telegram notification configuration; and
- saved stock-selection snapshots and review data.

The project does not operate a central service that collects or stores this
information.

## Credentials

API keys, access tokens, and other credentials are stored locally using the
application's credential/secrets storage mechanisms.

Credentials are not intentionally included in:

- Git commits;
- public data bundles;
- release artifacts; or
- external-AI clipboard exports.

## Third-party services

Data is sent to third-party services only when required for features the user
configures or explicitly invokes.

These services may include:

- FinMind;
- user-selected AI providers;
- LINE;
- Telegram; and
- market-data or news sources already documented by the project.

When using a third-party service, that service's own privacy policy and terms
apply.

## AI features

Built-in AI analysis sends the relevant analysis context to the AI provider
selected and configured by the user.

AI requests are made only through features that use the configured AI
provider.

The separate "Copy for External AI" feature does not make an AI API request.
It only formats information locally and copies it to the clipboard. The user
decides whether and where to paste that information.

Personal portfolio information is excluded from external-AI clipboard exports
by default and is included only when the user explicitly enables the
corresponding option.

## Notifications

If the user enables LINE or Telegram notifications, information necessary to
deliver the configured alert may be sent to the selected notification
service.

## Data bundles and releases

Private user data, credentials, portfolio information, and locally stored
secrets are not intended to be included in public data bundles or official
release artifacts.

## Telemetry

The project does not operate its own telemetry or analytics collection
service. Third-party libraries or services may have their own data practices;
this policy does not make claims about those practices.

## User control

Because the application is self-hosted, users control their local application
data and configuration. Users can remove locally stored application data using
the application's supported data/configuration mechanisms or by removing the
relevant local application data.

## Changes

This privacy policy may be updated when the application's data handling or
integrations change.

## Contact

For privacy questions, use the repository's GitHub Issues:

<https://github.com/nanachi1212/TWstockfor_tick-stock-panel/issues>

