# Secure Credential Persistence Pattern

This project currently prefers env-driven secrets and avoids storing credentials in local databases.
When server-side persistence is introduced (run configs, auth tokens, per-tenant credentials), use
an encrypt-at-rest envelope with key IDs from day one.

## Recommendation

- Encrypt sensitive config blobs before writing them to persistence.
- Store `key_id` with each encrypted record to support key rotation.
- Keep plaintext only in memory, for as little time as possible.
- Never log plaintext credentials.

## Reference Utility

Use `nuguard.common.secret_store.SecretCipher`:

- `encrypt_json(payload)` -> returns `EncryptedBlob` (`key_id`, `algorithm`, `ciphertext`, `created_at`)
- `decrypt_json(blob)` -> returns decrypted JSON object
- `rewrap(blob, new_active_key_id=...)` -> re-encrypt under a newer key

## Key Management

- Keep encryption keys outside source control.
- Use separate keys per environment.
- Rotate keys periodically; rewrap older blobs in migration jobs.
- Restrict key access to the persistence service process only.

## Rollout Guidance

1. Start with write-path encryption only.
2. Add read-path decryption and integrity checks.
3. Add periodic rewrap migration to current key.
4. Add audit events for key usage and decrypt failures.

This pattern is intentionally optional until a persistence feature requires it.
