//
// app/static/js/caddy-editor.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { autocompletion, completionKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { bracketMatching, foldGutter, foldKeymap, indentOnInput } from "@codemirror/language";
import { linter, lintGutter, lintKeymap } from "@codemirror/lint";
import { searchKeymap } from "@codemirror/search";
import { EditorState } from "@codemirror/state";
import { EditorView, drawSelection, keymap, lineNumbers } from "@codemirror/view";
import { scanBraces } from "./caddy-editor-braces.js";

const EDITOR_SELECTOR = "textarea[data-responsive-code-textarea]";
const MAX_LENGTH_ATTRIBUTE = "maxlength";
const SEARCH_MATCH_BACKGROUND = "var(--bs-warning-bg-subtle)";
const SEARCH_MATCH_SELECTED_BACKGROUND = "var(--bs-primary-bg-subtle)";

// Module-level WeakMap keeps EditorView off the DOM element (Finding 6).
const editorViews = new WeakMap();

const getCspNonce = () => {
    const meta = document.querySelector("meta[name='csp-nonce']");
    if (meta instanceof HTMLMetaElement && meta.content) {
        return meta.content;
    }
    return "";
};

const getLabelText = (textarea) =>
    Array.from(textarea.labels || [])
        .map((label) => label.textContent?.trim() || "")
        .find(Boolean);

const caddyDirectiveCompletions = [
    "abort",
    "basic_auth",
    "bind",
    "encode",
    "file_server",
    "handle",
    "handle_errors",
    "handle_path",
    "header",
    "header_down",
    "header_up",
    "import",
    "level",
    "log",
    "max_size",
    "output",
    "php_fastcgi",
    "redir",
    "request_body",
    "roll_size",
    "respond",
    "reverse_proxy",
    "root",
    "route",
    "format",
    "transport",
    "tls",
    "try_files",
    "uri",
];

// Pre-computed once; not recreated on every keystroke (Finding 7).
const caddyCompletionOptions = caddyDirectiveCompletions.map((label) => ({
    label,
    type: "keyword",
}));

const caddyCompletionSource = (context) => {
    const token = context.matchBefore(/[A-Za-z_][\w-]*/u);
    if (!token || (token.from === token.to && !context.explicit)) {
        return null;
    }

    const line = context.state.doc.lineAt(token.from);
    const beforeToken = context.state.sliceDoc(line.from, token.from);
    if (!/^\s*$/u.test(beforeToken)) {
        return null;
    }

    return {
        from: token.from,
        options: caddyCompletionOptions,
    };
};

const caddyTheme = EditorView.theme({
    "&": {
        border: "1px solid var(--cb-border)",
        borderRadius: "0.75rem",
        fontSize: "0.9rem",
        overflow: "hidden",
    },
    ".cm-scroller": {
        fontFamily: "ui-monospace, SFMono-Regular, Consolas, \"Liberation Mono\", Menlo, monospace",
        lineHeight: "1.5",
    },
    ".cm-content": {
        padding: "0.85rem 0",
    },
    ".cm-gutters": {
        backgroundColor: "var(--bs-tertiary-bg)",
        borderRight: "1px solid var(--cb-border)",
        color: "var(--cb-muted)",
    },
    ".cm-line": {
        padding: "0 0.85rem",
    },
    "&.cm-focused": {
        borderColor: "var(--cb-accent)",
        boxShadow: "0 0 0 0.2rem color-mix(in srgb, var(--cb-accent) 16%, transparent)",
        outline: "0",
    },
});

const caddySearchTheme = EditorView.theme({
    ".cm-searchMatch": {
        backgroundColor: SEARCH_MATCH_BACKGROUND,
    },
    ".cm-searchMatch-selected": {
        backgroundColor: SEARCH_MATCH_SELECTED_BACKGROUND,
    },
});

const readOnlyExtensions = (textarea) => {
    if (!textarea.readOnly && !textarea.disabled) {
        return [];
    }

    return [
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
    ];
};

const maxLengthExtension = (textarea) => {
    const maxLength = Number.parseInt(textarea.getAttribute(MAX_LENGTH_ATTRIBUTE) || "", 10);
    // Treat maxlength="0" as a real zero limit; only skip when absent or negative (Finding 1).
    if (!Number.isFinite(maxLength) || maxLength < 0) {
        return [];
    }

    return [
        EditorState.transactionFilter.of((transaction) => {
            if (!transaction.docChanged) {
                return transaction;
            }

            const currentLength = transaction.startState.doc.length;
            const nextLength = transaction.newDoc.length;
            if (nextLength <= maxLength || nextLength <= currentLength) {
                return transaction;
            }

            return [];
        }),
    ];
};

const dispatchTextareaInput = (textarea) => {
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
};

const createEditor = (textarea) => {
    const extensions = [
        lineNumbers(),
        foldGutter(),
        lintGutter(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        autocompletion({ override: [caddyCompletionSource] }),
        linter((view) => scanBraces(view.state.doc)),
        keymap.of([
            indentWithTab,
            ...defaultKeymap,
            ...historyKeymap,
            ...foldKeymap,
            ...searchKeymap,
            ...completionKeymap,
            ...lintKeymap,
        ]),
        caddyTheme,
        caddySearchTheme,
        ...maxLengthExtension(textarea),
        EditorView.updateListener.of((update) => {
            if (!update.docChanged) {
                return;
            }

            textarea.value = update.state.doc.toString();
            // Clear validation error state when the user edits (Finding 4).
            update.view.contentDOM.removeAttribute("aria-invalid");
            update.view.contentDOM.removeAttribute("aria-description");
            dispatchTextareaInput(textarea);
        }),
        ...readOnlyExtensions(textarea),
    ];

    const cspNonce = getCspNonce();
    if (cspNonce) {
        extensions.push(EditorView.cspNonce.of(cspNonce));
    }

    const view = new EditorView({
        state: EditorState.create({
            doc: textarea.value,
            extensions,
        }),
    });

    // Helper to push a value into CodeMirror from outside (Finding 2).
    const setEditorValue = (value) => {
        const currentValue = view.state.doc.toString();
        if (currentValue === value) {
            return;
        }

        view.dispatch({
            changes: {
                from: 0,
                to: view.state.doc.length,
                insert: value,
            },
        });
    };

    // Mirror ARIA attributes from the hidden textarea onto the visible editor (Finding 5).
    const describedBy = textarea.getAttribute("aria-describedby");
    if (describedBy) {
        view.contentDOM.setAttribute("aria-describedby", describedBy);
    }

    if (textarea.required) {
        view.contentDOM.setAttribute("aria-required", "true");
    }

    view.contentDOM.setAttribute("role", "textbox");
    view.contentDOM.setAttribute("aria-multiline", "true");
    view.contentDOM.setAttribute("aria-label", getLabelText(textarea) || "Caddy configuration editor");

    // Named handler references are required for later removal (Finding 3).
    const syncTextareaValue = () => {
        textarea.value = view.state.doc.toString();
    };

    const handleReset = () => {
        // After reset, the textarea value is restored to its default by the browser;
        // sync that value back into CodeMirror (Finding 2).
        queueMicrotask(() => setEditorValue(textarea.value));
    };

    const handleInvalid = (event) => {
        event.preventDefault();
        // Mirror validation failure visually on the editor for AT users (Finding 4).
        view.contentDOM.setAttribute("aria-invalid", "true");
        view.contentDOM.setAttribute(
            "aria-description",
            textarea.validationMessage || "Invalid editor content",
        );
        view.focus();
    };

    // Only intercept plain label clicks; leave modifier-key combos alone (Finding 8).
    const handleLabelClick = (event) => {
        if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }

        event.preventDefault();
        view.focus();
    };

    const form = textarea.form;
    if (form) {
        form.addEventListener("submit", syncTextareaValue);
        form.addEventListener("reset", handleReset);
    }

    textarea.addEventListener("invalid", handleInvalid);

    for (const label of textarea.labels || []) {
        label.addEventListener("click", handleLabelClick);
    }

    textarea.classList.add("code-editor-textarea-source");
    textarea.setAttribute("aria-hidden", "true");
    textarea.tabIndex = -1;
    textarea.after(view.dom);

    // Expose a destroy function for full cleanup on unmount (Finding 3).
    textarea.caddyBuddyDestroyCodeMirror = () => {
        if (form) {
            form.removeEventListener("submit", syncTextareaValue);
            form.removeEventListener("reset", handleReset);
        }

        textarea.removeEventListener("invalid", handleInvalid);

        for (const label of textarea.labels || []) {
            label.removeEventListener("click", handleLabelClick);
        }

        view.destroy();
        editorViews.delete(textarea);
        delete textarea.caddyBuddyDestroyCodeMirror;
        delete textarea.dataset.codemirrorInitialized;
    };

    // Store via WeakMap rather than as a raw DOM property (Finding 6).
    editorViews.set(textarea, view);
    textarea.dataset.codemirrorInitialized = "true";
    return view;
};

export function initialize() {
    for (const textarea of document.querySelectorAll(EDITOR_SELECTOR)) {
        if (!(textarea instanceof HTMLTextAreaElement) || textarea.dataset.codemirrorInitialized === "true") {
            continue;
        }
        createEditor(textarea);
    }
}

// Named export so callers don't need to reach into DOM custom properties (Finding 6).
export function getEditorView(textarea) {
    return editorViews.get(textarea) ?? null;
}

export { scanBraces };
