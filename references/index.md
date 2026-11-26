# Style references

Local copies of externally published style material. These are third-party
documents under their publishers' terms; they are not Nirnay source and are
not imported by build, test or runtime paths. They exist so style decisions
can be checked against published text rather than memory. The binding
repository rules remain in `STYLES.md` and the workspace lint configs.

| File | Source | SHA-256 |
|---|---|---|
| `cppguide.html` | <https://google.github.io/styleguide/cppguide.html> | `bdac9c1d0b5ddbc7622b696a8f049a6ca172a1dd2190d55b970afd1c15060c63` |
| `docguide-style.html` | <https://google.github.io/styleguide/docguide/style.html> | `c7657bd8a5bdb13e5172c4136af9003c4d28a56e1f78a4f56f0217cb9cdd8d95` |
| `jsoncguide.html` | <https://google.github.io/styleguide/jsoncguide.html> | `be7fdbbf2cca0d00e947ba6efbaef977a84c6b7d1cd371d5e2fceb2abcb23386` |
| `markdown-guide.html` | <https://github.com/qbists/style> | `9334da64ccb699bd7ca345f736f445b3c37c5c6b1890589a2a369ca6e129d276` |
| `pyguide.html` | <https://google.github.io/styleguide/pyguide.html> | `c81fe3405693a6b4f6e14d625b7176eab45754eef1ee76baae270abf15fec147` |
| `shellguide.html` | <https://google.github.io/styleguide/shellguide.html> | `7a5fe550de49a336fd7a4f8a5fbd78614054925443aa1c3df2bff1a98e593e30` |

`index.md` is this discovery index; no other files belong in `references/`.

## Scope notes

- This is a discovery index. Popularity or descriptions do not establish
  quality; no rule is adopted without a reviewed change.
- Published guides do not add a gate. The binding rules are the workspace
  checkers and lint configuration.
- https://google.github.io/styleguide/
- whttps://github.com/qbists/style
