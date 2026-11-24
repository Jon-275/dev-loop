- **Council drift warnings.** `devloop doctor` and `devloop council check` warn, without
  failing, when a dependency or file calls for a library pack no role card covers (for
  example `stripe` for payments), or a decision in `docs/decisions/` is enforced by no
  role. `council.declined` lists the packs the owner chose not to add.
