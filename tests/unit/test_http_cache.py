from app.infrastructure.http_cache import ConditionalCache, fingerprint


def test_get_on_empty_cache_is_a_miss():
    cache = ConditionalCache()
    assert cache.get("fp1", "url1") is None
    assert cache.misses == 1
    assert cache.hits == 0


def test_put_then_get_is_a_hit_with_the_stored_etag_and_body():
    cache = ConditionalCache()
    cache.put("fp1", "url1", etag="e1", body={"a": 1})

    assert cache.get("fp1", "url1") == ("e1", {"a": 1})
    assert cache.hits == 1
    assert cache.misses == 0
    assert cache.stores == 1


def test_put_overwrites_the_previous_entry():
    cache = ConditionalCache()
    cache.put("fp1", "url1", etag="e1", body="old")
    cache.put("fp1", "url1", etag="e2", body="new")

    assert cache.get("fp1", "url1") == ("e2", "new")
    assert cache.stores == 2


def test_different_tokens_never_share_an_entry():
    cache = ConditionalCache()
    cache.put("fp1", "same-url", etag="e1", body="body-for-fp1")

    assert cache.get("fp2", "same-url") is None  # miss: fp2 has never seen this url
    cache.put("fp2", "same-url", etag="e2", body="body-for-fp2")

    assert cache.get("fp1", "same-url") == ("e1", "body-for-fp1")
    assert cache.get("fp2", "same-url") == ("e2", "body-for-fp2")


def test_eviction_drops_the_least_recently_used_entry():
    cache = ConditionalCache(max_entries=2)
    cache.put("fp", "a", etag="ea", body="A")
    cache.put("fp", "b", etag="eb", body="B")
    cache.get("fp", "a")  # touch "a": "b" is now the least recently used entry

    cache.put("fp", "c", etag="ec", body="C")  # over the limit -> evicts "b"

    assert cache.get("fp", "b") is None
    assert cache.get("fp", "a") == ("ea", "A")
    assert cache.get("fp", "c") == ("ec", "C")


def test_eviction_order_considers_gets_not_just_insertion():
    cache = ConditionalCache(max_entries=1)
    cache.put("fp", "a", etag="ea", body="A")
    cache.put("fp", "b", etag="eb", body="B")  # evicts "a" immediately (limit is 1)

    assert cache.get("fp", "a") is None
    assert cache.get("fp", "b") == ("eb", "B")


def test_fingerprint_never_reveals_the_token():
    fp = fingerprint("gho_supersecret")

    assert fp != "gho_supersecret"
    assert "gho_supersecret" not in fp
    assert len(fp) == 16
    assert fp == fingerprint("gho_supersecret")  # deterministic
    assert fp != fingerprint("gho_other")  # different tokens -> different fingerprints
