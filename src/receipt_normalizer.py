"""Dify Code: validate a real fixed-SELECT plugin receipt, never driver errors.

No database, dependency, network or model calls. All output metadata originates
in the real plugin receipt. This does not verify clinical semantic correctness.
"""
import datetime
import hashlib
import json


MAX_RECEIPT_CHARS = 2000000
CONCEPT_FIELDS = (
    "concept_id", "concept_name", "vocabulary_id", "concept_code",
    "concept_class_id", "domain_id", "standard_concept", "valid_start_date",
    "valid_end_date", "invalid_reason",
)
RELATION_FIELDS = (
    "source_id", "target_id", "relationship_id", "valid_start_date",
    "valid_end_date", "invalid_reason",
)
SAFE_ERROR_CODES = frozenset((
    "invalid_terms", "invalid_batch_id", "receipt_too_large", "invalid_date",
    "invalid_plugin_envelope", "invalid_plugin_rows", "invalid_receipt_schema",
    "query_term_mismatch", "readonly_not_verified", "invalid_caps",
    "invalid_term_results", "invalid_result_count", "invalid_concepts",
    "invalid_concept_dates", "conflicting_concept_identity",
    "invalid_retrieval_evidence", "invalid_retrieval_counts",
    "invalid_missing_source_identities",
    "invalid_relationships", "invalid_standard_mapping_target",
    "duplicate_relationship", "invalid_vocabulary_versions",
))


def _parse(value):
    if isinstance(value, str):
        if len(value) > MAX_RECEIPT_CHARS:
            raise ValueError("receipt_too_large")
        return json.loads(value)
    return value


def _date(value):
    if not isinstance(value, str):
        raise ValueError("invalid_date")
    return datetime.date.fromisoformat(value).isoformat()


def _positive_id(value):
    return type(value) is int and value > 0


def _terms(value):
    result = _parse(value)
    if (not isinstance(result, list) or not 1 <= len(result) <= 8
            or any(not isinstance(term, str) or not term.strip()
                   or len(term) > 100 for term in result)):
        raise ValueError("invalid_terms")
    return result


def _plugin_rows(plugin_json, plugin_data, plugin_columns):
    """Accept documented json[0].data objects, or real data/columns arrays."""
    value = _parse(plugin_json)
    if value not in (None, [], ""):
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if isinstance(value, dict) and set(value) == {"data"}:
            rows = value["data"]
            if isinstance(rows, list):
                return rows
        raise ValueError("invalid_plugin_envelope")
    rows, columns = _parse(plugin_data), _parse(plugin_columns)
    if columns != ["receipt_text"] or not isinstance(rows, list):
        raise ValueError("invalid_plugin_envelope")
    if any(not isinstance(row, list) or len(row) != 1 for row in rows):
        raise ValueError("invalid_plugin_envelope")
    return [{"receipt_text": row[0]} for row in rows]


def _normalize(batch_id, terms, raw):
    if not isinstance(raw, dict) or raw.get("schema_version") != "ohdsi_sql_batch_v1":
        raise ValueError("invalid_receipt_schema")
    if raw.get("input_valid") is not True or raw.get("terms") != terms:
        raise ValueError("query_term_mismatch")
    verified = _date(raw.get("verification_date"))
    safety = raw.get("safety")
    if (not isinstance(safety, dict)
            or safety.get("read_only_set_result") != "on"
            or safety.get("transaction_read_only") != "on"
            or any(not isinstance(safety.get(key), str)
                   for key in ("statement_timeout", "lock_timeout"))
            or safety.get("statement_timeout_set_by_this_query") is not False):
        raise ValueError("readonly_not_verified")
    if (raw.get("cap") != 50 or raw.get("cap_scope") != "per_term"
            or raw.get("relationship_cap") != 400
            or type(raw.get("relationships_truncated")) is not bool
            or raw.get("exhaustive") is not False):
        raise ValueError("invalid_caps")
    stats = raw.get("term_results")
    if not isinstance(stats, list) or len(stats) != len(terms):
        raise ValueError("invalid_term_results")
    allowed_stages = {
        "not_needed_exact_hit", "attempted", "skipped_short_term",
        "skipped_preexisting_timeout_not_bounded",
    }
    for index, term in enumerate(terms, 1):
        row = stats[index - 1]
        if (not isinstance(row, dict) or type(row.get("term_index")) is not int
                or row.get("term_index") != index
                or row.get("term") != term or row.get("cap") != 50
                or type(row.get("returned_count")) is not int
                or not 0 <= row["returned_count"] <= 50
                or type(row.get("truncated")) is not bool
                or row.get("phrase_stage") not in allowed_stages
                or (row["truncated"] and row["returned_count"] != 50)):
            raise ValueError("invalid_term_results")
    total = sum(row["returned_count"] for row in stats)
    truncated = any(row["truncated"] for row in stats)
    if (type(raw.get("returned_count")) is not int
            or raw["returned_count"] != total
            or type(raw.get("truncated")) is not bool
            or raw["truncated"] != truncated):
        raise ValueError("invalid_result_count")
    records = raw.get("concepts")
    if not isinstance(records, list) or len(records) > 800:
        raise ValueError("invalid_concepts")
    identities = {}
    for row in records:
        if not isinstance(row, dict) or any(key not in row for key in CONCEPT_FIELDS):
            raise ValueError("invalid_concepts")
        clean = {key: row[key] for key in CONCEPT_FIELDS}
        concept_id = clean["concept_id"]
        if (not _positive_id(concept_id)
                or any(not isinstance(clean[key], str) or not clean[key]
                       for key in ("concept_name", "vocabulary_id",
                                   "concept_class_id", "domain_id"))
                or not isinstance(clean["concept_code"], (str, type(None)))
                or clean["standard_concept"] not in (None, "S", "C")
                or clean["invalid_reason"] not in (None, "D", "U")):
            raise ValueError("invalid_concepts")
        if _date(clean["valid_start_date"]) > _date(clean["valid_end_date"]):
            raise ValueError("invalid_concept_dates")
        if concept_id in identities and identities[concept_id] != clean:
            raise ValueError("conflicting_concept_identity")
        identities[concept_id] = clean
    # SQL explicitly distinguishes a synonym hit with no concept row.
    # A hit is not a verified identity, and cannot authorize any binding/path.
    missing = raw.get("missing_source_identities", [])
    if not isinstance(missing, list) or len(missing) > 400:
        raise ValueError("invalid_missing_source_identities")
    missing_ids = set()
    for row in missing:
        if (not isinstance(row, dict) or set(row) != {"concept_id", "reason"}
                or not _positive_id(row.get("concept_id"))
                or row["concept_id"] in identities or row["concept_id"] in missing_ids
                or row["reason"] != "concept_row_missing"):
            raise ValueError("invalid_missing_source_identities")
        missing_ids.add(row["concept_id"])
    evidence = raw.get("retrieval_evidence")
    if not isinstance(evidence, list) or len(evidence) != total:
        raise ValueError("invalid_retrieval_evidence")
    counts, seen, source_ids = [0] * len(terms), set(), set()
    for row in evidence:
        if not isinstance(row, dict):
            raise ValueError("invalid_retrieval_evidence")
        index, concept_id = row.get("term_index"), row.get("concept_id")
        if (type(index) is not int or not 1 <= index <= len(terms)
                or row.get("term") != terms[index - 1]
                or not _positive_id(concept_id)
                or concept_id not in (set(identities) | missing_ids)
                or (index, concept_id) in seen
                or not isinstance(row.get("matched_text"), str)
                or row.get("matched_field") not in ("concept_name", "concept_synonym_name")
                or row.get("match_mode") not in (
                    "exact", "exact_case_insensitive", "literal_case_insensitive_substring")
                or type(row.get("rank")) is not int or not 1 <= row["rank"] <= 50):
            raise ValueError("invalid_retrieval_evidence")
        seen.add((index, concept_id))
        counts[index - 1] += 1
        source_ids.add(concept_id)
    if missing_ids != (source_ids - set(identities)):
        raise ValueError("invalid_missing_source_identities")
    # Sources without identity must not supply Maps to paths, even if a target
    # is real and standard. Keep every raw evidence hit for truthful accounting.
    source_ids -= missing_ids
    identity_rejections = [dict(row, reason="concept_row_missing",
                                binding_eligible=False)
                           for row in evidence if row["concept_id"] in missing_ids]
    usable_counts = [{"term_index": index, "term": term,
                      "identity_backed_hit_count": sum(
                          row["term_index"] == index and row["concept_id"] in identities
                          for row in evidence),
                      "identity_rejected_hit_count": sum(
                          row["term_index"] == index and row["concept_id"] in missing_ids
                          for row in evidence)}
                     for index, term in enumerate(terms, 1)]
    if counts != [row["returned_count"] for row in stats]:
        raise ValueError("invalid_retrieval_counts")
    raw_relations = raw.get("relationships")
    if not isinstance(raw_relations, list) or len(raw_relations) > 400:
        raise ValueError("invalid_relationships")
    relationships, relationship_seen = [], set()
    for row in raw_relations:
        if not isinstance(row, dict) or any(key not in row for key in RELATION_FIELDS):
            raise ValueError("invalid_relationships")
        item = {key: row[key] for key in RELATION_FIELDS}
        source, target = item["source_id"], item["target_id"]
        start, end = _date(item["valid_start_date"]), _date(item["valid_end_date"])
        if (not _positive_id(source) or source not in source_ids
                or not _positive_id(target) or target not in identities
                or item["relationship_id"] not in ("Maps to", "Maps to value")
                or item["invalid_reason"] is not None or not start <= verified <= end):
            raise ValueError("invalid_relationships")
        target_meta = identities[target]
        if (target_meta["standard_concept"] != "S"
                or target_meta["invalid_reason"] is not None
                or not target_meta["valid_start_date"] <= verified <= target_meta["valid_end_date"]):
            raise ValueError("invalid_standard_mapping_target")
        item["evidence_id"] = "|".join((str(source), item["relationship_id"],
                                          str(target), start, end))
        if item["evidence_id"] in relationship_seen:
            raise ValueError("duplicate_relationship")
        relationship_seen.add(item["evidence_id"])
        relationships.append(item)
    versions = raw.get("vocabulary_versions")
    if not isinstance(versions, list):
        raise ValueError("invalid_vocabulary_versions")
    version_ids = set()
    for row in versions:
        if (not isinstance(row, dict) or not isinstance(row.get("vocabulary_id"), str)
                or not isinstance(row.get("vocabulary_version"), (str, type(None)))
                or row["vocabulary_id"] in version_ids):
            raise ValueError("invalid_vocabulary_versions")
        version_ids.add(row["vocabulary_id"])
    # The real database can contain incomplete custom concepts or concepts whose
    # vocabulary row is absent. Preserve their actual metadata and make defects
    # explicit; do not abort unrelated, complete vocabulary identities. The final
    # selection gate must reject such identities, including locally marked S.
    metadata_warnings = []
    if missing_ids:
        metadata_warnings.append({
            "code": "missing_source_concept_metadata",
            "concept_ids": sorted(missing_ids), "binding_eligible": False,
            "interpretation": "retrieval hit has no concept identity; cannot bind; not zero results",
        })
    for row in identities.values():
        if not row["concept_code"] or not row["concept_code"].strip():
            metadata_warnings.append({"code": "missing_concept_code",
                                      "concept_ids": [row["concept_id"]],
                                      "vocabulary_id": row["vocabulary_id"]})
    for vocabulary_id in sorted({row["vocabulary_id"] for row in identities.values()} - version_ids):
        metadata_warnings.append({
            "code": "missing_vocabulary_metadata", "vocabulary_id": vocabulary_id,
            "concept_ids": sorted(row["concept_id"] for row in identities.values()
                                  if row["vocabulary_id"] == vocabulary_id),
        })
    for row in versions:
        if not row["vocabulary_version"] or not row["vocabulary_version"].strip():
            metadata_warnings.append({
                "code": "empty_vocabulary_version", "vocabulary_id": row["vocabulary_id"],
                "concept_ids": sorted(item["concept_id"] for item in identities.values()
                                      if item["vocabulary_id"] == row["vocabulary_id"]),
            })
    return {
        "batch_id": batch_id, "terms": terms, "verification_date": verified,
        "cap": 50, "cap_scope": "per_term", "returned_count": total,
        "truncated": truncated, "term_results": stats, "error": None,
        "concepts": list(identities.values()), "relationships": relationships,
        "vocabulary_versions": versions, "retrieval_evidence": evidence,
        "metadata_warnings": metadata_warnings,
        "identity_rejections": identity_rejections,
        "missing_source_identities": missing,
        "identity_hit_counts": usable_counts,
        "identity_backed_hit_count": sum(row["identity_backed_hit_count"] for row in usable_counts),
        "usable_returned_count": sum(row["identity_backed_hit_count"] for row in usable_counts),
        "usable_returned_count_semantics": "has actual concept metadata only; not standard or semantic eligibility",
        "count_semantics": "returned_count and term_results count all real retrieval hits; identity-backed counts do not imply standard or semantic eligibility",
        "relationships_truncated": raw["relationships_truncated"],
        "relationship_cap": 400, "safety": safety,
        "retrieval_policy": raw.get("retrieval_policy"), "exhaustive": False,
        "note": "Actual bounded vocabulary receipt; skipped phrase stages and caps must remain visible; no semantic correctness claim",
    }



MEANING_SCHEMA = "ohdsi_meaning_e4_v1"
MEANING_RELATIONS = (
    "Is a", "Has status", "Has finding site", "Has interprets",
    "Has interpretation", "Has asso morph", "Has causative agent",
    "Has severity", "Has laterality", "Part of",
)
SAFE_ERROR_CODES = SAFE_ERROR_CODES | frozenset((
    "invalid_input_request", "input_request_mismatch", "invalid_meaning_schema",
    "invalid_meaning_scope", "invalid_meaning_bundle", "invalid_meaning_identity",
    "invalid_meaning_version", "invalid_meaning_synonyms", "invalid_meaning_relations",
    "invalid_meaning_availability", "invalid_meaning_counts", "meaning_lexical_contamination",
))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _request(value):
    raw = _parse(value)
    legacy = isinstance(raw, list)
    if legacy:
        return {"mode": "search", "terms": _terms(raw)}, True
    if not isinstance(raw, dict):
        raise ValueError("invalid_input_request")
    mode = raw.get("mode")
    if mode == "search" and set(raw) == {"mode", "terms"}:
        terms = raw["terms"]
        if (not isinstance(terms, list) or not 1 <= len(terms) <= 6
                or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in terms)):
            raise ValueError("invalid_input_request")
        return {"mode": mode, "terms": terms}, False
    if mode == "meaning" and set(raw) == {"mode", "concept_ids"}:
        ids = raw["concept_ids"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 6
                or any(not _positive_id(x) or x > 2147483647 for x in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("invalid_input_request")
        return {"mode": mode, "concept_ids": ids}, False
    raise ValueError("invalid_input_request")


def _active(identity, verified):
    return (identity["invalid_reason"] is None
            and identity["valid_start_date"] <= verified <= identity["valid_end_date"])


def _meaning_identity(row, versions):
    if not isinstance(row, dict) or set(row) != set(CONCEPT_FIELDS) | {"vocabulary_version"}:
        raise ValueError("invalid_meaning_identity")
    # Reuse the same field contract, without requiring standard status for a
    # meaning endpoint: Has status legitimately points at nonstandard metadata.
    if (not _positive_id(row["concept_id"])
            or any(not isinstance(row[k], str) or not row[k]
                   for k in ("concept_name", "vocabulary_id", "domain_id", "concept_class_id"))
            or not isinstance(row["concept_code"], (str, type(None)))
            or row["standard_concept"] not in (None, "S", "C")
            or row["invalid_reason"] not in (None, "D", "U")
            or _date(row["valid_start_date"]) > _date(row["valid_end_date"])):
        raise ValueError("invalid_meaning_identity")
    if (not isinstance(row["vocabulary_version"], (str, type(None)))
            or row["vocabulary_version"] != versions.get(row["vocabulary_id"])):
        raise ValueError("invalid_meaning_version")
    return dict(row)


def _meaning_extension(raw, receipt, request):
    if (raw.get("extension_schema_version") != MEANING_SCHEMA
            or raw.get("mode") != request["mode"]
            or raw.get("input_request") != request):
        raise ValueError("input_request_mismatch")
    mode, verified = request["mode"], receipt["verification_date"]
    if mode == "meaning" and any(raw.get(k) != [] for k in (
            "terms", "concepts", "relationships", "retrieval_evidence", "term_results")):
        raise ValueError("meaning_lexical_contamination")
    if mode == "meaning" and raw.get("returned_count") != 0:
        raise ValueError("meaning_lexical_contamination")
    scope = raw.get("meaning_scope")
    scope_keys = {"mode", "selection_policy", "selected_concept_ids", "eligible_count", "cap", "truncated"}
    if (not isinstance(scope, dict) or set(scope) != scope_keys
            or scope["mode"] != mode or scope["cap"] != 6
            or type(scope["truncated"]) is not bool):
        raise ValueError("invalid_meaning_scope")
    versions = {v["vocabulary_id"]: v["vocabulary_version"] for v in receipt["vocabulary_versions"]}
    identities = {c["concept_id"]: c for c in receipt["concepts"]}
    if mode == "search":
        old_policy = "eligible_exact_standard_by_min_term_index_then_concept_id"
        mapped_policy = "eligible_exact_standard_or_maps_to_target_by_min_term_index_then_concept_id"
        if scope["selection_policy"] not in (old_policy, mapped_policy):
            raise ValueError("invalid_meaning_scope")
        # Recompute from validated exact hits and actual outgoing Maps to rows;
        # a mapping path is an enrichment trigger, never a source equivalence.
        mapped_targets = {}
        if scope["selection_policy"] == mapped_policy:
            for rel in receipt["relationships"]:
                if rel["relationship_id"] == "Maps to":
                    mapped_targets.setdefault(rel["source_id"], []).append(rel["target_id"])
        ranked = {}
        for hit in receipt["retrieval_evidence"]:
            if (hit["concept_id"] not in identities
                    or hit["match_mode"] not in ("exact", "exact_case_insensitive")):
                continue
            ids = [hit["concept_id"]] + mapped_targets.get(hit["concept_id"], [])
            for cid in ids:
                c = identities.get(cid)
                if (c and c["standard_concept"] == "S" and _active(c, verified)
                        and c["concept_code"] and c["concept_code"].strip()
                        and versions.get(c["vocabulary_id"]) and versions[c["vocabulary_id"]].strip()):
                    ranked[cid] = min(ranked.get(cid, hit["term_index"]), hit["term_index"])
        eligible = sorted(ranked, key=lambda cid: (ranked[cid], cid))
        selected = eligible[:6]
        if (type(scope["eligible_count"]) is not int
                or scope["eligible_count"] != len(eligible)
                or scope["truncated"] != (len(eligible) > 6)):
            raise ValueError("invalid_meaning_scope")
    else:
        selected = request["concept_ids"]
        if (scope["selection_policy"] != "explicit_requested_concept_ids"
                or scope["eligible_count"] is not None or scope["truncated"] is not False):
            raise ValueError("invalid_meaning_scope")
    if scope["selected_concept_ids"] != selected:
        raise ValueError("invalid_meaning_scope")
    raw_bundles = raw.get("meaning_evidence")
    if not isinstance(raw_bundles, list) or len(raw_bundles) != len(selected):
        raise ValueError("invalid_meaning_bundle")
    bundles = []
    for cid, bundle in zip(selected, raw_bundles):
        keys = {"schema_version", "concept_id", "source", "verification_date", "synonyms", "relations",
                "availability", "caps", "counts", "truncated"}
        if (not isinstance(bundle, dict) or set(bundle) != keys
                or bundle["schema_version"] != MEANING_SCHEMA
                or bundle["concept_id"] != cid or type(bundle["concept_id"]) is not int
                or bundle["verification_date"] != verified
                or bundle["caps"] != {"synonyms": 12, "direct_relations": 10}):
            raise ValueError("invalid_meaning_bundle")
        source = None if bundle["source"] is None else _meaning_identity(bundle["source"], versions)
        if source is not None and source["concept_id"] != cid:
            raise ValueError("invalid_meaning_identity")
        if mode == "search" and (source is None or {k: source[k] for k in CONCEPT_FIELDS} != identities.get(cid)):
            raise ValueError("invalid_meaning_identity")
        syns, rels = bundle["synonyms"], bundle["relations"]
        if not isinstance(syns, list) or len(syns) > 12:
            raise ValueError("invalid_meaning_synonyms")
        if not isinstance(rels, list) or len(rels) > 10:
            raise ValueError("invalid_meaning_relations")
        seen_syn, seen_rel = set(), set()
        for syn in syns:
            if (not isinstance(syn, dict) or set(syn) != {"concept_id", "term", "language_concept_id", "source_table", "description_type"}
                    or source is None or syn["concept_id"] != cid or type(syn["concept_id"]) is not int
                    or not isinstance(syn["term"], str) or not syn["term"].strip()
                    or not (syn["language_concept_id"] is None or type(syn["language_concept_id"]) is int and syn["language_concept_id"] >= 0)
                    or syn["source_table"] != "concept_synonym"
                    or syn["description_type"] != "not_stored_in_source_schema"):
                raise ValueError("invalid_meaning_synonyms")
            key = (syn["term"], syn["language_concept_id"])
            if key in seen_syn:
                raise ValueError("invalid_meaning_synonyms")
            seen_syn.add(key)
        rel_keys = {"source_id", "target_id", "relationship_id", "direction", "source_identity", "target_identity",
                    "valid_start_date", "valid_end_date", "invalid_reason", "relationship_name", "is_hierarchical",
                    "defines_ancestry", "reverse_relationship_id", "relationship_concept_id"}
        endpoint_versions = []
        for rel in rels:
            if not isinstance(rel, dict) or set(rel) != rel_keys:
                raise ValueError("invalid_meaning_relations")
            si = _meaning_identity(rel["source_identity"], versions)
            ti = _meaning_identity(rel["target_identity"], versions)
            start, finish = _date(rel["valid_start_date"]), _date(rel["valid_end_date"])
            if (source is None or si != source or rel["source_id"] != cid or type(rel["source_id"]) is not int
                    or not _positive_id(rel["target_id"]) or rel["target_id"] != ti["concept_id"]
                    or rel["relationship_id"] not in MEANING_RELATIONS or rel["direction"] != "source_to_target"
                    or rel["invalid_reason"] is not None or not start <= verified <= finish
                    or not _active(source, verified) or not _active(ti, verified)
                    or any(not isinstance(rel[k], str) or not rel[k] for k in ("relationship_name", "reverse_relationship_id"))
                    or rel["is_hierarchical"] not in ("0", "1") or rel["defines_ancestry"] not in ("0", "1")
                    or not _positive_id(rel["relationship_concept_id"])):
                raise ValueError("invalid_meaning_relations")
            key = (cid, rel["relationship_id"], rel["target_id"], start, finish)
            if key in seen_rel:
                raise ValueError("invalid_meaning_relations")
            seen_rel.add(key)
            endpoint_versions.append(ti["vocabulary_version"])
        counts, trunc, availability = bundle["counts"], bundle["truncated"], bundle["availability"]
        if (not isinstance(counts, dict) or set(counts) != {"synonyms_total", "direct_relations_total"}
                or not isinstance(trunc, dict) or set(trunc) != {"synonyms", "direct_relations"}):
            raise ValueError("invalid_meaning_counts")
        for name, totalkey, cap, rows in (("synonyms", "synonyms_total", 12, syns), ("direct_relations", "direct_relations_total", 10, rels)):
            total = counts[totalkey]
            if (type(total) is not int or total < 0 or len(rows) != min(total, cap)
                    or type(trunc[name]) is not bool or trunc[name] != (total > cap)):
                raise ValueError("invalid_meaning_counts")
        expected_availability = {
            "concept_identity": "available" if source is not None else "not_found",
            "synonyms": "available" if syns else "no_rows",
            "direct_relations": "available" if rels else "no_rows",
            "vocabulary_metadata": "available" if source is not None and source["vocabulary_version"]
                and source["vocabulary_version"].strip() and all(v and v.strip() for v in endpoint_versions) else "unavailable",
            "description_type": "not_stored_in_source_schema",
            "formal_text_definition": "not_stored_in_source_schema",
            "relationship_group": "not_stored_in_source_schema",
        }
        if availability != expected_availability or source is None and (syns or rels or any(counts.values())):
            raise ValueError("invalid_meaning_availability")
        item = json.loads(_canonical(bundle))
        # Validate raw endpoint provenance above, then remove only the repeated
        # source object. Its full identity/version remains at bundle.source.
        for relation in item["relations"]:
            relation.pop("source_identity")
        item["meaning_ref"] = "meaning:" + hashlib.sha256(_canonical(item).encode("utf-8")).hexdigest()[:24]
        bundles.append(item)
    return bundles, scope


def main(batch_id: str, terms_json: str, plugin_json=None,
         plugin_data=None, plugin_columns=None) -> dict:
    # The existing Dify selectors stay unchanged: terms_json now carries the
    # Code-owned typed envelope, still a String. No driver error text is copied.
    request, terms = None, []
    safe_batch_id = batch_id if isinstance(batch_id, str) and len(batch_id) <= 80 else "invalid_batch"
    try:
        if not isinstance(batch_id, str) or not batch_id or len(batch_id) > 80:
            raise ValueError("invalid_batch_id")
        request, legacy = _request(terms_json)
        terms = request.get("terms", [])
        rows = _plugin_rows(plugin_json, plugin_data, plugin_columns)
        if len(rows) != 1 or not isinstance(rows[0], dict) or set(rows[0]) != {"receipt_text"}:
            raise ValueError("invalid_plugin_rows")
        raw = _parse(rows[0]["receipt_text"])
        receipt = _normalize(batch_id, terms, raw)
        if legacy and isinstance(raw, dict) and "extension_schema_version" not in raw:
            bundles, scope = [], {"mode": "search", "selection_policy": "legacy_no_meaning_extension",
                "selected_concept_ids": [], "eligible_count": None, "cap": 6, "truncated": False}
        else:
            bundles, scope = _meaning_extension(raw, receipt, request)
        receipt.update(mode=request["mode"], input_request=request,
                       meaning_scope=scope, meaning_evidence=bundles,
                       meaning_schema_version=MEANING_SCHEMA,
                       meaning_evidence_semantics="actual untyped lexical and direct-relationship evidence; not text definition or source equivalence")
        return {"batch_receipt_json": json.dumps(receipt, ensure_ascii=False), "query_ok": True, "error_code": ""}
    except Exception as error:
        safe_code = str(error) if type(error) is ValueError and str(error) in SAFE_ERROR_CODES else "invalid_query_receipt"
        receipt = {"batch_id": safe_batch_id, "mode": request.get("mode") if request else None,
                   "input_request": request, "terms": terms, "verification_date": None,
                   "cap": 50, "cap_scope": "per_term", "returned_count": 0, "truncated": False,
                   "term_results": [], "error": "query_failed", "concepts": [], "relationships": [],
                   "vocabulary_versions": [], "retrieval_evidence": [], "metadata_warnings": [],
                   "meaning_scope": None, "meaning_evidence": [], "error_code": safe_code, "exhaustive": False}
        return {"batch_receipt_json": json.dumps(receipt, ensure_ascii=False), "query_ok": False, "error_code": safe_code}
