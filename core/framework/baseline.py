import statistics

CLASSES = ("draft", "internal_write", "read")


def _summary_invalid(summary):
    if not isinstance(summary, dict):
        raise ValueError("summary_invalid")
    if summary.get("ok") is not True:
        raise ValueError("summary_not_verified")
    verify = summary.get("verify")
    if not isinstance(verify, dict) or verify.get("ok") is not True:
        raise ValueError("summary_not_verified")
    inner = summary.get("summary")
    if not isinstance(inner, dict):
        raise ValueError("summary_invalid")
    by_class = inner.get("by_class")
    if not isinstance(by_class, dict):
        raise ValueError("summary_invalid")
    for bucket in by_class.values():
        if not isinstance(bucket, dict):
            raise ValueError("summary_invalid")
        for count in bucket.values():
            if type(count) is not int or count < 0:
                raise ValueError("summary_invalid")
    if "observations" in inner:
        observations = inner["observations"]
        if not isinstance(observations, dict):
            raise ValueError("summary_invalid")
        if set(observations.keys()) != {
            "gate_mismatch",
            "gate_unseen",
            "gate_ignored",
            "other",
        }:
            raise ValueError("summary_invalid")
        for value in observations.values():
            if type(value) is not int or value < 0:
                raise ValueError("summary_invalid")
    if "approvals" in inner:
        approvals = inner["approvals"]
        if not isinstance(approvals, dict):
            raise ValueError("summary_invalid")
        if set(approvals.keys()) != {"granted", "denied"}:
            raise ValueError("summary_invalid")
        for value in approvals.values():
            if type(value) is not int or value < 0:
                raise ValueError("summary_invalid")
    if "approvals_by_class" in inner:
        by_approval = inner["approvals_by_class"]
        if not isinstance(by_approval, dict):
            raise ValueError("summary_invalid")
        for value in by_approval.values():
            if not isinstance(value, dict):
                raise ValueError("summary_invalid")
            if set(value.keys()) != {"granted", "denied"}:
                raise ValueError("summary_invalid")
            for count in value.values():
                if type(count) is not int or count < 0:
                    raise ValueError("summary_invalid")
        if "approvals" not in inner:
            raise ValueError("summary_invalid")
        total = inner["approvals"]
        sum_granted = sum(bucket["granted"] for bucket in by_approval.values())
        sum_denied = sum(bucket["denied"] for bucket in by_approval.values())
        if total["granted"] != sum_granted or total["denied"] != sum_denied:
            raise ValueError("summary_invalid")


def _validate_value_list(values, minimum, maximum):
    if not isinstance(values, list) or len(values) > 10000:
        raise ValueError("timesheet_invalid")
    for entry in values:
        if type(entry) is not int or entry < minimum or entry > maximum:
            raise ValueError("timesheet_invalid")


def _timesheet_invalid(timesheet):
    if not isinstance(timesheet, dict):
        raise ValueError("timesheet_invalid")
    version = timesheet.get("schema_version")
    if version not in (1, 2):
        raise ValueError("timesheet_invalid")
    if timesheet.get("unit") != "minutes":
        raise ValueError("timesheet_invalid")
    if version == 1:
        allowed_keys = {"schema_version", "unit", "samples"}
    else:
        allowed_keys = {"schema_version", "unit", "samples", "review_samples"}
    if set(timesheet.keys()) != allowed_keys:
        raise ValueError("timesheet_invalid")
    samples = timesheet.get("samples")
    if not isinstance(samples, dict) or set(samples.keys()) != set(CLASSES):
        raise ValueError("timesheet_invalid")
    for key in CLASSES:
        _validate_value_list(samples[key], 1, 480)
    if version == 2:
        review = timesheet.get("review_samples")
        if not isinstance(review, dict) or set(review.keys()) != set(CLASSES):
            raise ValueError("timesheet_invalid")
        for key in CLASSES:
            _validate_value_list(review[key], 0, 480)


def _median(values):
    med = statistics.median(values)
    if med == int(med):
        return int(med)
    return med


def _product(allow, factor):
    value = allow * factor
    if isinstance(factor, int) and isinstance(allow, int):
        if value == int(value):
            return int(value)
    if value == int(value):
        return int(value)
    return float(value)


def _attach_net_fields(entry, schema_version, review_values, min_samples):
    n_review = len(review_values) if schema_version == 2 else 0
    entry["review_samples"] = n_review
    if schema_version == 1 or n_review == 0:
        entry["net_basis"] = "check_time_not_measured"
        entry["median_review_minutes"] = None
        entry["net_minutes"] = None
        return
    median_review = _median(review_values)
    if n_review < min_samples:
        entry["net_basis"] = "zu_wenig_messungen"
        entry["median_review_minutes"] = median_review
        entry["net_minutes"] = None
        return
    if entry["basis"] != "gemessen":
        entry["net_basis"] = "handarbeit_nicht_gemessen"
        entry["median_review_minutes"] = median_review
        entry["net_minutes"] = None
        return
    entry["median_review_minutes"] = median_review
    delta = entry["median_minutes"] - median_review
    entry["net_basis"] = "gemessen"
    entry["net_minutes"] = _product(entry["allow"], delta)


def compute(summary, timesheet, min_samples):
    if type(min_samples) is not int or min_samples < 1 or min_samples > 1000:
        raise ValueError("min_samples_invalid")
    _summary_invalid(summary)
    _timesheet_invalid(timesheet)
    inner = summary["summary"]
    by_class = inner["by_class"]
    samples = timesheet["samples"]
    schema_version = timesheet["schema_version"]
    review_samples = timesheet.get("review_samples", {}) if schema_version == 2 else {}

    excluded: dict[str, int] = {}
    for special in ("action_class_unknown", "no_context"):
        bucket = by_class.get(special)
        if isinstance(bucket, dict):
            for outcome, count in bucket.items():
                excluded[outcome] = excluded.get(outcome, 0) + count

    approvals_by_class = inner.get("approvals_by_class")
    classes: dict = {}
    measured_parts: list[int] = []
    any_measured = False
    for key in CLASSES:
        bucket = by_class.get(key, {})
        if not isinstance(bucket, dict):
            bucket = {}
        allow = bucket.get("allow", 0)
        not_executed = {outcome: count for outcome, count in bucket.items() if outcome != "allow"}
        sample_values = samples[key]
        n = len(sample_values)
        if n == 0:
            entry = {
                "allow": allow,
                "basis": "nicht_gemessen",
                "manual_minutes": None,
                "median_minutes": None,
                "not_executed": not_executed,
                "samples": 0,
            }
        elif n < min_samples:
            median = _median(sample_values)
            entry = {
                "allow": allow,
                "basis": "zu_wenig_messungen",
                "manual_minutes": None,
                "median_minutes": median,
                "not_executed": not_executed,
                "samples": n,
            }
        else:
            median = _median(sample_values)
            manual = _product(allow, median)
            entry = {
                "allow": allow,
                "basis": "gemessen",
                "manual_minutes": manual,
                "median_minutes": median,
                "not_executed": not_executed,
                "samples": n,
            }
            measured_parts.append(manual)
            any_measured = True
        reviews = review_samples.get(key, []) if schema_version == 2 else []
        _attach_net_fields(entry, schema_version, reviews, min_samples)
        if approvals_by_class is None:
            entry["approvals"] = None
        else:
            bucket = approvals_by_class.get(key, {"granted": 0, "denied": 0})
            granted = bucket.get("granted", 0)
            denied = bucket.get("denied", 0)
            total = granted + denied
            rate = None if total == 0 else round(granted / total, 4)
            entry["approvals"] = {"granted": granted, "denied": denied, "rate": rate}
        if approvals_by_class is None:
            entry["approved_executed"] = None
            entry["manual_minutes_approved"] = None
            entry["net_minutes_approved"] = None
        else:
            approval_bucket = approvals_by_class.get(key, {"granted": 0, "denied": 0})
            executed = approval_bucket.get("granted", 0)
            entry["approved_executed"] = executed
            if entry["basis"] == "gemessen":
                entry["manual_minutes_approved"] = _product(executed, entry["median_minutes"])
            else:
                entry["manual_minutes_approved"] = None
            if entry["net_basis"] == "gemessen":
                delta = entry["median_minutes"] - entry["median_review_minutes"]
                entry["net_minutes_approved"] = _product(executed, delta)
            else:
                entry["net_minutes_approved"] = None
        classes[key] = entry

    manual_total = sum(measured_parts) if any_measured else None
    if manual_total is not None and manual_total == int(manual_total):
        manual_total = int(manual_total)

    net_incomplete = sorted(
        key for key in CLASSES if classes[key]["allow"] > 0 and classes[key]["net_basis"] != "gemessen"
    )
    net_values = [classes[key]["net_minutes"] for key in CLASSES if classes[key]["net_basis"] == "gemessen"]
    if net_incomplete or not net_values:
        net_total = None
    else:
        net_total = sum(net_values)
        if net_total == int(net_total):
            net_total = int(net_total)

    observations = None
    observations_clean = None
    if "observations" in inner:
        observations = dict(inner["observations"])
        observations_clean = (
            observations["gate_mismatch"] == 0
            and observations["gate_unseen"] == 0
            and observations["gate_ignored"] == 0
        )

    approvals = None
    if "approvals" in inner:
        approvals = dict(inner["approvals"])

    approvals_unlinked = None
    if approvals_by_class is not None:
        unlink = approvals_by_class.get("unlinked", {"granted": 0, "denied": 0})
        approvals_unlinked = unlink.get("granted", 0) + unlink.get("denied", 0)

    approved_incomplete = None
    manual_minutes_approved_total = None
    net_minutes_approved_total = None
    if approvals_by_class is not None:
        approved_incomplete = sorted(
            key
            for key in CLASSES
            if classes[key]["approved_executed"] > 0
            and (
                classes[key]["basis"] != "gemessen"
                or classes[key]["net_basis"] != "gemessen"
            )
        )
        if not approved_incomplete and (approvals_unlinked or 0) == 0:
            manual_parts = [
                classes[key]["manual_minutes_approved"]
                for key in CLASSES
                if classes[key]["approved_executed"] > 0
            ]
            net_parts = [
                classes[key]["net_minutes_approved"]
                for key in CLASSES
                if classes[key]["approved_executed"] > 0
            ]
            manual_minutes_approved_total = sum(manual_parts) if manual_parts else 0
            net_minutes_approved_total = sum(net_parts) if net_parts else 0
            if manual_minutes_approved_total == int(manual_minutes_approved_total):
                manual_minutes_approved_total = int(manual_minutes_approved_total)
            if net_minutes_approved_total == int(net_minutes_approved_total):
                net_minutes_approved_total = int(net_minutes_approved_total)

    return {
        "approvals": approvals,
        "approved_incomplete": approved_incomplete,
        "approvals_unlinked": approvals_unlinked,
        "classes": classes,
        "excluded": excluded,
        "manual_minutes_approved_total": manual_minutes_approved_total,
        "manual_minutes_total": manual_total,
        "min_samples": min_samples,
        "net_incomplete": net_incomplete,
        "net_minutes_approved_total": net_minutes_approved_total,
        "net_minutes_total": net_total,
        "observations": observations,
        "observations_clean": observations_clean,
        "since": summary.get("since"),
        "unit": "minutes",
        "until": summary.get("until"),
    }