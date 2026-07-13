# Mudae list and search flags

MOA treats Mudae flags as typed query semantics rather than opaque letters.
Use `moa command flags` to browse the reference or explain a combined query:

```powershell
moa command explain '$mmwy= Re:Zero$--Some bundle'
```

For imported keyed-harem evidence, the equivalent MOA-side search is:

```powershell
moa catalog harem --server "Lake Arrowhead 2025" --account "ernieuuu" `
  --series "Re:Zero" --sort keys --min-keys 5
```

`--sort keys` corresponds to the `$mmy=` ordering, `--sort kakera` to `k=`,
and `--min-keys` expresses a numeric `y>` filter. This searches only imported
keyed-harem evidence; it does not claim that an unimported character is owned.

For imported `$top` ranks, account evidence can be cross-referenced explicitly:

```powershell
moa catalog top --limit 50 `
  --server "Lake Arrowhead 2025" --account "ernieuuu" `
  --unavailable-only
```

`--keyed-only` means a matching keyed-harem observation exists. It is not an
`o+`/owned filter for every character because MOA does not yet have complete
ownership evidence for unkeyed characters. `--unavailable-only` is based only
on direct `$topx` observations.

The parser uses longest-match semantics, so `w+` is the wishlist flag while
`w` is the waifu gender filter. It also recognizes numeric forms such as
`z<5`, `y>7`, and `y!<3`.

Mudae separates multiple search arguments with `$`. Prefixing an argument with
`--` excludes that character, series, tag, bundle, or note from the search.

Important distinctions for future MOA query features:

- `o=` means owned-only, `o+` means owned by you, and `o-` means not owned by you.
- `u` means unclaimed; it is not the same as “not owned by me.”
- `x-` means not disabled, while `z-` means without spheres and `y-` means without keys.
- Display and sort flags are separate: `k` displays Kakera values, while `k=` sorts by
  Kakera. Likewise, `y` selects keyed characters, `y=` sorts by key count, and
  `y+` keeps the full `$mm` list while displaying keys.
- `$mmyk` means keyed characters with Kakera values displayed; `$mmrk` means
  rank-sorted output with rank and Kakera shown; `$mmrk=` changes the ordering
  to Kakera-sorted while retaining both displays.
- `k-` and `y-` are filters for values/keys, not sort modes.
- Missing imported harem keys do not prove that a character is unowned. MOA
  must retain an explicit unknown state until ownership evidence is imported.
