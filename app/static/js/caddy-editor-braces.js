//
// app/static/js/caddy-editor-braces.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

export const scanBraces = (doc) => {
    const diagnostics = [];
    const stack = [];
    let offset = 0;

    for (const line of doc.toString().split("\n")) {
        let inQuote = false;
        let escaped = false;

        for (let index = 0; index < line.length; index += 1) {
            const character = line[index];

            if (escaped) {
                escaped = false;
                continue;
            }
            if (inQuote && character === "\\") {
                escaped = true;
                continue;
            }
            if (character === "\"") {
                inQuote = !inQuote;
                continue;
            }
            if (!inQuote && character === "#") {
                break;
            }
            if (inQuote) {
                continue;
            }

            const position = offset + index;
            if (character === "{") {
                stack.push(position);
            } else if (character === "}") {
                const openingPosition = stack.pop();
                if (openingPosition === undefined) {
                    diagnostics.push({
                        from: position,
                        to: position + 1,
                        severity: "error",
                        message: "Closing brace has no matching opening brace.",
                    });
                }
            }
        }

        offset += line.length + 1;
    }

    for (const openingPosition of stack) {
        diagnostics.push({
            from: openingPosition,
            to: openingPosition + 1,
            severity: "error",
            message: "Opening brace has no matching closing brace.",
        });
    }

    return diagnostics;
};
