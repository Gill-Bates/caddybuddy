//
// tools/ui-lint/lib/device-page-pool.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

export function createDevicePagePool({
    browser,
    buildContextOptions,
    deviceContextOptions,
    installAnalyzers,
    installLayoutShiftObserver,
    installUiLintInitScript,
    storageState,
}) {
    const resources = new Map();
    const pending = new Map();

    async function resolvePendingResources() {
        const pendingResources = await Promise.allSettled(pending.values());
        return pendingResources
            .filter((entry) => entry.status === 'fulfilled')
            .map((entry) => entry.value);
    }

    async function createResource(device) {
        const baseOptions = deviceContextOptions.get(device) || deviceContextOptions.get('desktop') || {};
        const context = await browser.newContext(buildContextOptions({
            ...baseOptions,
            storageState,
        }));
        await installUiLintInitScript(context);
        await installAnalyzers(context);
        await installLayoutShiftObserver(context);

        const page = await context.newPage();
        await page.emulateMedia({ reducedMotion: 'reduce' });
        return { context, page };
    }

    return {
        async getPage(device = 'desktop') {
            const resolvedDevice = deviceContextOptions.has(device) ? device : 'desktop';
            if (resources.has(resolvedDevice)) {
                return resources.get(resolvedDevice).page;
            }
            if (pending.has(resolvedDevice)) {
                const resource = await pending.get(resolvedDevice);
                return resource.page;
            }

            const resourcePromise = createResource(resolvedDevice)
                .then((resource) => {
                    resources.set(resolvedDevice, resource);
                    pending.delete(resolvedDevice);
                    return resource;
                })
                .catch((error) => {
                    pending.delete(resolvedDevice);
                    throw error;
                });

            pending.set(resolvedDevice, resourcePromise);
            const resource = await resourcePromise;
            return resource.page;
        },

        async closeAll() {
            const pendingResources = await resolvePendingResources();
            const contexts = new Set([
                ...Array.from(resources.values(), (resource) => resource.context),
                ...pendingResources.map((resource) => resource.context),
            ]);
            resources.clear();
            pending.clear();
            await Promise.allSettled(Array.from(contexts, (context) => context.close()));
        },
    };
}