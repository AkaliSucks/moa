# MOA profiles

MOA keeps user-specific server and account selection outside the repository and
outside the imported SQLite catalog, in a local config file. A profile can
contain one simple server/account pair or many server/account identities,
including alts.

```powershell
moa config account add --server "Lake Arrowhead 2025" --account "ernieuuu" --role primary
moa config account add --server "Lake Arrowhead 2025" --account "ernie_alt" --role alt
moa config use --server "Lake Arrowhead 2025" --account "ernieuuu"
moa config show
```

Named profiles are useful when the same person has separate setups:

```powershell
moa config profile add travel
moa config account add --profile travel --server "Travel Server" --account "travel_account"
moa config use --profile travel --server "Travel Server" --account "travel_account"
```

Explicit `--server` and `--account` values remain overrides. Catalog top
currently uses the active context and all configured primary/alt identities on
that server when deciding whether a `$topo` owner belongs to you; the same
resolver will be applied to the remaining account commands.
