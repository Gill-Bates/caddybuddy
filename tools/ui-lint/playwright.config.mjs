import { defineConfig, devices } from '@playwright/test';


export default defineConfig({
    testDir: './tests',
    testMatch: '**/*.spec.{ts,mjs,js}',
    timeout: 60_000,
    expect: {
        timeout: 10_000,
    },
    fullyParallel: true,
    workers: undefined,
    forbidOnly: Boolean(process.env.CI),
    retries: process.env.CI ? 1 : 0,
    reporter: [
        ['list'],
        ['html', { open: 'never', outputFolder: 'test-results/report' }],
    ],
    outputDir: 'test-results',
    use: {
        actionTimeout: 10_000,
        navigationTimeout: 20_000,
        baseURL: process.env.UI_LINT_BASE_URL || 'http://127.0.0.1:8000',
        headless: true,
        screenshot: 'only-on-failure',
        trace: 'retain-on-failure',
        video: 'retain-on-failure',
    },
    projects: [
        // Desktop Coverage (Finding 2)
        {
            name: 'desktop-chromium',
            use: {
                ...devices['Desktop Chrome'],
                browserName: 'chromium',
            },
        },
        {
            name: 'desktop-firefox',
            use: {
                ...devices['Desktop Firefox'],
                browserName: 'firefox',
            },
        },
        {
            name: 'desktop-safari',
            use: {
                ...devices['Desktop Safari'],
                browserName: 'webkit',
            },
        },

        // Mobile Coverage
        {
            name: 'mobile-chromium',
            use: {
                ...devices['Pixel 5'],
                browserName: 'chromium',
            },
        },
        {
            name: 'mobile-webkit',
            use: {
                ...devices['iPhone 13'],
                browserName: 'webkit',
            },
        },
        {
            name: 'mobile-webkit-se',
            use: {
                ...devices['iPhone SE'],
                browserName: 'webkit',
            },
        },
    ],
    /*
     * Finding 5 Note: WebKit on Linux is a proxy for iOS Safari but does not
     * perfectly replicate iOS-specific behaviors (touch-action, dynamic UI chrome).
     * Desktop Safari is included as a closer proxy for WebKit-specific rendering.
     */
});