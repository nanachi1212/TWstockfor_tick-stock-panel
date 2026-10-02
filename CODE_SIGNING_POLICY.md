# Windows Code Signing Policy

## Status

Windows code signing for this repository is **pending SignPath Foundation
approval and configuration**. The locally built launcher and any release
artifact produced before approval are unsigned and must not be presented as a
signed official release.

## Scope

Only official Windows release artifacts built from this repository by the
approved GitHub Actions release workflow are eligible for signing. Local
builds, pull-request artifacts, arbitrary branch builds, and binaries from
outside this repository are not official signed release artifacts.

The current release workflow is `.github/workflows/release.yml`. It builds the
Windows installer from the repository source and publishes it as a GitHub
Release asset. Signing is not active in that workflow yet.

## Project eligibility and provenance

- This is an individual-maintainer open-source project released under the MIT
  license.
- The official source repository is
  `https://github.com/nanachi1212/TWstockfor_tick-stock-panel`.
- Official Windows release artifacts must retain GitHub Actions build
  provenance from this repository and may be signed only through the approved
  CI path.

## Signing controls

- Signing will be performed only by approved CI after the SignPath Foundation
  project, signing policy, artifact configuration, and approval rules are in
  place.
- No private signing keys, certificates, certificate passwords, or SignPath
  API tokens are stored in this repository.
- CI credentials must remain in GitHub Actions secrets or the approved signing
  provider. They must never be committed to source, workflow files, release
  assets, or logs.
- The unsigned artifact must be uploaded to GitHub Actions before a signing
  request is submitted. The signed artifact must be verified before it is
  published as the official Windows release asset.
- The signing request must be restricted to the official release path and
  must preserve source and build provenance.

## Approval and release roles

Until SignPath Foundation approval is complete, the repository maintainer is
responsible for source changes and release publication, but no release is
claimed to be code-signed. After approval, the SignPath project and its policy
define the permitted submitter, reviewer, and signing approver roles. The
repository must not bypass those controls by signing locally or with a
self-signed certificate.

## Required verification

Before describing a Windows artifact as signed, the release process must
record:

1. `Get-AuthenticodeSignature` reports `Status: Valid`.
2. The signer certificate and its chain are trusted on supported Windows
   systems.
3. The signed artifact is the exact artifact produced by the approved CI
   build and has not been replaced after signing.
4. A real Windows smoke test can launch the signed artifact and reach the
   documented backend and frontend health checks.

## Configuration required after approval

The following values must be supplied by SignPath and stored only in the
approved GitHub Actions configuration:

- SignPath organization ID
- SignPath project slug or ID
- SignPath signing policy slug or ID
- SignPath artifact configuration slug or ID, if the project uses one
- A SignPath API token with only the submitter permissions required by the
  project policy, stored as `SIGNPATH_API_TOKEN`
- The approved GitHub Actions origin/build policy and release trigger

The repository contains a non-triggering reusable workflow scaffold at
`.github/workflows/signpath-windows-release.yml`. It intentionally requires
these values as workflow inputs and secrets; it contains no project IDs,
credentials, certificate material, or active release trigger.

