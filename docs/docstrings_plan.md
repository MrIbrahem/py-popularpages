Convert all docstrings in the codebase from the short Sphinx-style format (using `:param:` and `:return:`) to the expanded Google-style format (using `Args:` and `Returns:` with a detailed explanatory paragraph at the top of the docstring).

For every function containing a docstring in the old style:

1. Add an expanded description paragraph (2–4 sentences) that explains the function's purpose and behavior in detail, replacing the terse one-liner.
2. Convert `:param name: ...` into an `Args:` section formatted as:
    ```
    Args:
        name (type): Description.
    ```
3. Convert `:return: ...` into a `Returns:` section formatted as:
    ```
    Returns:
        type: Description.
    ```
4. Preserve the exact same meaning and information from the original — don't drop any detail, just rephrase and reformat.
5. Apply this transformation to every `.py` file in the project without exception, leaving the rest of the code untouched.
