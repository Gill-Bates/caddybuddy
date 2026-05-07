//
// tools/ui-lint/browser/analyzers/accessibility.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

export function checkAltText(elements, isVisible) {
    return elements
        .filter(el => el.tagName === 'IMG')
        .filter(isVisible)
        .filter(img => !img.hasAttribute('alt'))
        .map(img => ({ src: img.getAttribute('src') }));
}
