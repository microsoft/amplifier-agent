## Network Tab improvements

The Network tab in the UI mode and trace viewer has several nice improvements:

- filtering by asset type and URL
- better display of query string parameters
- preview of font assets

![Network tab now has filters](https://github.com/user-attachments/assets/4bd1b67d-90bd-438b-a227-00b9e86872e2)

Credit to @kubajanik for these wonderful improvements!

## `--tsconfig` CLI option

By default, Playwright will look up the closest tsconfig for each imported file using a heuristic. You can now specify a single tsconfig file in the command line, and Playwright will use it for all imported files, not only test files:

```sh
# Pass a specific tsconfig
npx playwright test --tsconfig tsconfig.test.json
```

## [APIRequestContext](https://playwright.dev/docs/api/class-apirequestcontext) now accepts [`URLSearchParams`](https://developer.mozilla.org/en-US/docs/Web/API/URLSearchParams) and `string` as query parameters

You can now pass [`URLSearchParams`](https://developer.mozilla.org/en-US/docs/Web/API/URLSearchParams) and `string` as query parameters to [APIRequestContext](https://playwright.dev/docs/api/class-apirequestcontext):

```ts
test('query params', async ({ request }) => {
  const searchParams = new URLSearchParams();
  searchParams.set('userId', 1);
  const response = await request.get(
      'https://jsonplaceholder.typicode.com/posts',
      {
        params: searchParams // or as a string: 'userId=1'
      }
  );
  // ...
});
```

## Miscellaneous
- The `mcr.microsoft.com/playwright:v1.47.0` now serves a Playwright image based on Ubuntu 24.04 Noble.
  To use the 22.04 jammy-based image, please use `mcr.microsoft.com/playwright:v1.47.0-jammy` instead.
- The `:latest`/`:focal`/`:jammy` tag for Playwright Docker images is no longer being published. Pin to a specific version for better stability and reproducibility.
- New option `behavior` in [page.removeAllListeners()](https://playwright.dev/docs/api/class-page#page-remove-all-listeners), [browser.removeAllListeners()](https://playwright.dev/docs/api/class-browser#browser-remove-all-listeners) and [browserContext.removeAllListeners()](https://playwright.dev/docs/api/class-browsercontext#browser-context-remove-all-listeners) to wait for ongoing listeners to complete.
- TLS client certificates can now be passed from memory by passing `cert` and `key` as buffers instead of file paths.
- Attachments with a `text/html` content type can now be opened in a new tab in the HTML report. This is useful for including third-party reports or other HTML content in the Playwright test report and distributing it to your team.
- `noWaitAfter` in [locator.selectOption()](https://playwright.dev/docs/api/class-locator#locator-select-option) was deprecated.
- We've seen reports of WebGL in Webkit misbehaving on GitHub Actions `macos-13`. We recommend upgrading GitHub Actions to `macos-14`.

## Browser Versions
- Chromium 129.0.6668.29
- Mozilla Firefox 130.0
- WebKit 18.0

This version was also tested against the following stable channels:
- Google Chrome 128
- Microsoft Edge 128

