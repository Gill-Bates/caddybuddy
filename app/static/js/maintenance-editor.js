//
// app/static/js/maintenance-editor.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

// Lightweight WYSIWYG editor for the maintenance page (Settings -> General).
// Progressive enhancement: without JavaScript the raw HTML textarea stays usable.
// The server sanitizes the submitted HTML, so the editor only handles formatting.

(() => {
    "use strict";

    const SAFE_LINK_PATTERN = /^(https?:\/\/|mailto:)/i;
    const BLOCK_TAGS = new Set(["h1", "h2", "p"]);

    const currentBlockTag = (content) => {
        const selection = document.getSelection();
        let node = selection && selection.rangeCount ? selection.anchorNode : null;
        while (node && node !== content) {
            const tag = node.nodeName.toLowerCase();
            if (BLOCK_TAGS.has(tag)) {
                return tag;
            }
            node = node.parentNode;
        }
        return null;
    };

    const selectionInside = (content) => {
        const selection = document.getSelection();
        return Boolean(selection && selection.rangeCount && content.contains(selection.anchorNode));
    };

    const initialize = () => {
        const form = document.querySelector("[data-maintenance-editor]");
        if (!form) {
            return;
        }
        const toolbar = form.querySelector("[data-maintenance-editor-toolbar]");
        const content = form.querySelector("[data-maintenance-editor-content]");
        const source = form.querySelector("[data-maintenance-editor-source]");
        const label = form.querySelector("#maintenance-page-label");
        if (!toolbar || !content || !source) {
            return;
        }

        content.innerHTML = source.value;
        source.hidden = true;
        source.required = false;
        toolbar.hidden = false;
        content.hidden = false;
        document.execCommand("defaultParagraphSeparator", false, "p");

        const updateToolState = () => {
            if (!selectionInside(content)) {
                return;
            }
            const blockTag = currentBlockTag(content);
            toolbar.querySelectorAll("[data-editor-command]").forEach((button) => {
                let active = false;
                try {
                    active = document.queryCommandState(button.dataset.editorCommand);
                } catch {
                    // Unsupported command: leave the button unpressed.
                }
                button.setAttribute("aria-pressed", String(active));
            });
            toolbar.querySelectorAll("[data-editor-block]").forEach((button) => {
                button.setAttribute("aria-pressed", String(button.dataset.editorBlock === blockTag));
            });
        };

        const run = (command, value = null) => {
            content.focus();
            document.execCommand(command, false, value);
            updateToolState();
        };

        // Keep the text selection when a toolbar button is clicked with the mouse.
        toolbar.addEventListener("mousedown", (event) => {
            if (event.target.closest("button")) {
                event.preventDefault();
            }
        });

        toolbar.addEventListener("click", (event) => {
            const button = event.target.closest("button");
            if (!button) {
                return;
            }
            if (button.dataset.editorCommand) {
                run(button.dataset.editorCommand);
            } else if (button.dataset.editorBlock) {
                run("formatBlock", `<${button.dataset.editorBlock}>`);
            } else if (button.hasAttribute("data-editor-link")) {
                const url = window.prompt("Link URL (https://, http:// or mailto:)", "https://");
                if (url === null) {
                    return;
                }
                const trimmed = url.trim();
                if (!SAFE_LINK_PATTERN.test(trimmed)) {
                    window.alert("Links must start with https://, http:// or mailto:.");
                    return;
                }
                run("createLink", trimmed);
            }
        });

        if (label) {
            label.addEventListener("click", (event) => {
                event.preventDefault();
                content.focus();
            });
        }

        document.addEventListener("selectionchange", updateToolState);

        form.addEventListener("submit", () => {
            source.value = content.innerHTML.trim();
        });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();
