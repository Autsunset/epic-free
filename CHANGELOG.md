# Changelog

## 0.5.1 - 2026-10-30

### Fixed

- Support both combined and email-first Epic login forms, including challenges
  shown before the real password form and either login submit button.
- Ignore off-screen autofill fields and unfinished challenge frames; bound the
  challenge-frame replacement wait instead of retrying a missing frame.
- Finish the store's separate SSO exchange after account authentication. Select
  a saved account only when its email uniquely matches the configured account,
  and require the store's authenticated state before reporting success.
- Stop retrying explicit verification rejections and avoid waiting indefinitely
  for account-confirmation prompts that no longer appear.
- Preserve generic Gemini relay base URLs without appending `/gemini`.

### Changed

- Pin Camoufox to 0.5.8 and the tested official Linux browser to
  158.0.1-beta.41. This browser is an upstream prerelease, deliberately pinned
  for compatibility; downloads verify the publisher's SHA-256 digest.
- Use the verified browser in Docker and scheduled GitHub Actions runs. Install
  frozen dependencies and avoid dependency synchronization at container startup.
- Add opt-in `OPENAI_REASONING_EFFORT` and `OPENAI_MAX_TOKENS` controls for
  Chat Completions providers that support them.
- Restrict the Docker build context to public build inputs and ignore private
  environment overrides, local tooling settings and deployment backups.

### Verification

- 125 offline unit tests pass, covering login forms, store SSO, challenge
  readiness, browser archive verification and provider request limits.
- Fresh authentication and the already-owned-game collection path are verified
  in a deployed Docker environment. No new checkout was exercised in that run.
- This release does not include credentials, browser profiles, runtime logs,
  screenshots or deployment-specific gateway configuration.
