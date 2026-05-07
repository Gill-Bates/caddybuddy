//
// tools/ui-lint/browser/utils/dom-cache.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

let cache = null;

export function getAllElements() {
    if (!cache) {
        cache = Array.from(document.querySelectorAll('*'));
    }
    return cache;
}
