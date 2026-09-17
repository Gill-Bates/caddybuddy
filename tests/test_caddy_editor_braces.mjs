//
// tests/test_caddy_editor_braces.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import assert from 'node:assert/strict';
import test from 'node:test';

import { scanBraces } from '../app/static/js/caddy-editor-braces.js';

// Minimal stand-in for the CodeMirror Text document passed by the linter.
const createDoc = (source) => ({
    toString() {
        return source;
    },
});

// Fixtures use *unbalanced* braces on purpose: a balanced "{ ... }" pair would
// pass even if the quote/comment guards were removed.

test('scanBraces ignores an unbalanced brace inside a quoted string', () => {
    const diagnostics = scanBraces(createDoc('respond "literal { brace"\n'));
    assert.deepEqual(diagnostics, []);
});

test('scanBraces treats escaped quotes as string content', () => {
    const diagnostics = scanBraces(createDoc('respond "escaped \\" and { brace"\n'));
    assert.deepEqual(diagnostics, []);
});

test('scanBraces ignores an unbalanced brace after a comment marker', () => {
    const diagnostics = scanBraces(createDoc('respond ok # {\n'));
    assert.deepEqual(diagnostics, []);
});

test('scanBraces ignores braces inside nested directive arguments', () => {
    const diagnostics = scanBraces(createDoc(`
reverse_proxy 10.30.0.12:3000 {
    header_up X-Real-IP {remote_host}
    header_up X-Forwarded-Port {port}
}
`));

    assert.deepEqual(diagnostics, []);
});

test('scanBraces reports the position of an unclosed opening brace', () => {
    const diagnostics = scanBraces(createDoc('route {\n    respond ok\n'));
    assert.deepEqual(diagnostics, [{
        from: 6,
        to: 7,
        severity: 'error',
        message: 'Opening brace has no matching closing brace.',
    }]);
});

test('scanBraces reports the position of an unmatched closing brace', () => {
    const diagnostics = scanBraces(createDoc('respond ok\n}\n'));
    assert.deepEqual(diagnostics, [{
        from: 11,
        to: 12,
        severity: 'error',
        message: 'Closing brace has no matching opening brace.',
    }]);
});
