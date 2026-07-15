## TLS Client Certificates

Playwright now allows to supply client-side certificates, so that server can verify them, as specified by TLS Client Authentication.

When client certificates are specified, all browser traffic is routed through a proxy that establishes the secure TLS connection, provides client certificates to the server and validates server certificates.

The following snippet sets up a client certificate for `https://example.com`:

```ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  // ...
  use: {
    clientCertificates: [{
      origin: 'https://example.com',
      certPath: './cert.pem',
      keyPath: './key.pem',
      passphrase: 'mysecretpassword',
    }],
  },
  // ...
});
```

You can also provide client certificates to a particular [test project](https://playwright.dev/docs/api/class-testproject#test-project-use) or as a parameter of [browser.newContext()](https://playwright.dev/docs/api/class-browser#browser-new-context) and [apiRequest.newContext()](https://playwright.dev/docs/api/class-apirequest#api-request-new-context).

## `--only-changed` cli option

New CLI option `--only-changed` allows to only run test files that have been changed since the last git commit or from a specific git "ref".

```sh
# Only run test files with uncommitted changes
npx playwright test --only-changed

# Only run test files changed relative to the "main" branch
npx playwright test --only-changed=main
```

## Component Testing: New `router` fixture

This release introduces an experimental `router` fixture to intercept and handle network requests in component testing.
There are two ways to use the router fixture:
- Call `router.route(url, handler)` that behaves similarly to [page.route()](https://playwright.dev/docs/api/class-page#page-route).
- Call `router.use(handlers)` and pass [MSW library](https://mswjs.io) request handlers to it.

Here is an example of reusing your existing MSW handlers in the test.

```ts
import { handlers } from '@src/mocks/handlers';

test.beforeEach(async ({ router }) => {
  // install common handlers before each test
  await router.use(...handlers);
});

test('example test', async ({ mount }) => {
  // test as usual, your handlers are active
  // ...
});
```

This fixture is only available in [component tests](https://playwright.dev/docs/test-components#handling-network-requests).

## UI Mode / Trace Viewer Updates
- Test annotations are now shown in UI mode.
- Content of text attachments is now rendered inline in the attachments pane.
- New setting to show/hide routing actions like [route.continue()](https://playwright.dev/docs/api/class-route#route-continue).
- Request method and status are shown in the network details tab.
- New button to copy source file location to clipboard.
- Metadata pane now displays the `baseURL`.

## Miscellaneous
- New `maxRetries` option in [apiRequestContext.fetch()](https://playwright.dev/docs/api/class-apirequestcontext#api-request-context-fetch) which retries on the `ECONNRESET` network error.
- New option to [box a fixture](https://playwright.dev/docs/test-fixtures#box-fixtures) to minimize the fixture exposure in test reports and error messages.
- New option to provide a [custom fixture title](https://playwright.dev/docs/test-fixtures#custom-fixture-title) to be used in test reports and error messages.

## Possibly breaking change

Fixture values that are array of objects, when specified in the `test.use()` block, may require being wrapped into a fixture tuple. This is best seen on the example:

```ts
import { test as base } from '@playwright/test';

// Define an option fixture that has an "array of objects" value
type User = { name: string, password: string };
const test = base.extend<{ users: User[] }>({
  users: [ [], { option: true } ],
}); 

// Specify option value in the test.use block.
test.use({
  // WRONG: this syntax may not work for you
  users: [
    { name: 'John Doe', password: 'secret' },
    { name: 'John Smith', password: 's3cr3t' },
  ],
  // CORRECT: this syntax will work. Note extra [] around the value, and the "scope" property.
  users: [[
    { name: 'John Doe', password: 'secret' },
    { name: 'John Smith', password: 's3cr3t' },
  ], { scope: 'test' }],
});

test('example test', async () => {
  // ...
});
```

## Browser Versions
- Chromium 128.0.6613.18
- Mozilla Firefox 128.0
- WebKit 18.0

This version was also tested against the following stable channels:
- Google Chrome 127
- Microsoft Edge 127

