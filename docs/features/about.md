# About Page

The About page gives a logged-in user a quick operational snapshot of the running instance.

## Application details

Shows the application name, version, short build commit, Python version, and the display timezone.

## Check for updates

Queries the GitHub releases API for the latest published version and compares it against the running version. The result is cached for one hour. A forced, cache-bypassing check is available only to administrators.

## Dependencies

Lists the resolved versions of the key runtime packages installed in the image.

## Changelog

Renders `CHANGELOG.md` as sanitized HTML directly on the page.
