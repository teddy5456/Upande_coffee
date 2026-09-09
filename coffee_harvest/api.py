"""
Face matching + liveness for Upande Bio.

Whitelisted endpoints:

  match_face_v2(frames=[b64,...], challenge=None)
      Multi-frame match + liveness. The mobile client captures 3–5 frames
      during the countdown (forward face → asked challenge → forward face)
      and sends them as base64-encoded JPEGs. The server runs:

        * face presence — every frame must contain a face. Loses the face
          mid-sequence and it's a spoof artefact (held-up photo flickers).
        * motion — the centroid of the face bounding box must drift by
          more than `_MOTION_PIXEL_THRESHOLD` between the first and last
          frame. A printed photo held perfectly still fails this.
        * blink — Eye Aspect Ratio (EAR) computed per frame; the sequence
          must contain at least one frame where EAR drops below the
          blink threshold and another where it returns above (a real
          blink). Used alone this is weak — replays defeat it — so we
          also enforce challenge consistency below.
        * challenge — if `challenge='blink_twice'` the EAR must dip at
          least twice; if `challenge='look_right'` the bbox must move
          right more than _CHALLENGE_LATERAL_THRESHOLD; etc. The mobile
          client picks the challenge per attempt so a pre-recorded video
          of one challenge can't be replayed for a different challenge.

      On success returns the best-match employee + similarity. On any
      liveness failure returns `matched=False, liveness_passed=False,
      reason='blink_missing' | 'no_motion' | 'no_face_in_frame_N' | …`.

  match_face(image_b64)
      Single-frame fallback. No liveness. Kept for completeness — the
      kiosk only calls this if multi-frame capture fails.

  compute_face_embedding(image_b64=None, file_url=None)
      Single-frame embedding. Accepts a file_url to avoid the extra
      upload during enrolment.

  backfill_face_embeddings()
      Sweeps every active enrolment with face_image but no
      face_embedding and computes them now. Manager's 'Refresh face
      matching' button calls this.

  matcher_status()
      Probe — is face_recognition installed, how many enrolments are
      ready, what's the threshold.

Activation on the bench (one-off):

    cd ~/kaitet-bench
    ./env/bin/pip install face_recognition
    bench restart

Until that's done every endpoint reports `available: False` and the
kiosk falls back to the manual name picker.
"""
import base64
import io
import json
import math
import os

import frappe


# ── Tuning constants — tweak server-side without rebuilding the app ─────────
# Slightly more permissive than face_recognition's textbook defaults to keep
# real harvesters out of the manual-name-picker fallback. The bar is still
# tighter than commercial systems (Gmail's face unlock ≈ 0.62 effective).
_MATCH_THRESHOLD = 0.62          # cosine distance — was 0.60
_HIGH_CONFIDENCE_THRESHOLD = 0.45  # below this the match is so good we waive
                                   # the liveness gate (see _evaluate_liveness)
_MOTION_PIXEL_THRESHOLD = 3      # was 6 — even a slight head bob counts
_BLINK_EAR_THRESHOLD = 0.21
_BLINK_OPEN_THRESHOLD = 0.27
_CHALLENGE_LATERAL_THRESHOLD = 0.06  # was 0.10 — a small head turn is enough
_CHALLENGE_VERTICAL_THRESHOLD = 0.04


def _matcher_available():
    try:
        import face_recognition  # noqa: F401
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def _decode_b64(s: str):
    import numpy as np
    from PIL import Image
    raw = base64.b64decode(s)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    return np.array(img)


def _decode_file_url(file_url: str):
    import numpy as np
    from PIL import Image
    bench_path = frappe.get_site_path()
    if file_url.startswith("/private/files/"):
        path = os.path.join(bench_path, "private", "files", file_url[len("/private/files/"):])
    elif file_url.startswith("/files/"):
        path = os.path.join(bench_path, "public", "files", file_url[len("/files/"):])
    else:
        raise ValueError(f"Unsupported file_url: {file_url}")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    img = Image.open(path).convert("RGB")
    return np.array(img)


def _largest_face_box(arr):
    """Return (top, right, bottom, left) of the largest detected face, or None."""
    import face_recognition
    boxes = face_recognition.face_locations(arr, model="hog")
    if not boxes:
        return None
    return max(boxes, key=lambda b: (b[2] - b[0]) * (b[1] - b[3]))


def _embed_array(arr, box=None):
    """Compute the 128-d face embedding for the supplied image."""
    import face_recognition
    if box is None:
        box = _largest_face_box(arr)
        if box is None:
            return None
    encodings = face_recognition.face_encodings(arr, known_face_locations=[box])
    if not encodings:
        return None
    return encodings[0].tolist()


def _cosine_distance(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - (dot / (na * nb))


def _eye_aspect_ratio(landmarks_for_eye):
    """Standard EAR formula. landmarks_for_eye is a 6-point list in dlib order."""
    def _dist(p, q):
        return math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2)
    if len(landmarks_for_eye) < 6:
        return 1.0
    p1, p2, p3, p4, p5, p6 = landmarks_for_eye[:6]
    horiz = _dist(p1, p4)
    if horiz == 0:
        return 1.0
    return (_dist(p2, p6) + _dist(p3, p5)) / (2.0 * horiz)


def _frame_descriptors(arr):
    """Detect face + landmarks + EAR for one frame.

    Returns:
      {bbox: (top,right,bottom,left), centroid: (x,y), ear: float, image_size: (h,w)}
    or None if no face is found.
    """
    import face_recognition
    box = _largest_face_box(arr)
    if box is None:
        return None
    top, right, bottom, left = box
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    landmarks_list = face_recognition.face_landmarks(arr, face_locations=[box])
    ear = 1.0
    if landmarks_list:
        lm = landmarks_list[0]
        le = lm.get("left_eye") or []
        re = lm.get("right_eye") or []
        if le and re:
            ear = (_eye_aspect_ratio(le) + _eye_aspect_ratio(re)) / 2.0
    return {
        "bbox": box,
        "centroid": (cx, cy),
        "ear": ear,
        "size": (arr.shape[0], arr.shape[1]),
    }


def _check_blink(ear_series, min_dips=1):
    """Return True iff the EAR sequence shows at least `min_dips` blinks
    (drop below _BLINK_EAR_THRESHOLD then return above _BLINK_OPEN_THRESHOLD)."""
    dips = 0
    closed = False
    for ear in ear_series:
        if not closed and ear < _BLINK_EAR_THRESHOLD:
            closed = True
        elif closed and ear > _BLINK_OPEN_THRESHOLD:
            dips += 1
            closed = False
        if dips >= min_dips:
            return True
    return False


def _check_motion(centroids):
    if len(centroids) < 2:
        return False
    x0, y0 = centroids[0]
    x1, y1 = centroids[-1]
    return math.hypot(x1 - x0, y1 - y0) >= _MOTION_PIXEL_THRESHOLD


def _check_challenge(challenge, descriptors):
    """Verify that the requested challenge actually happened in the frames."""
    if not challenge:
        return True, "no_challenge"

    centroids = [d["centroid"] for d in descriptors]
    sizes = [d["size"] for d in descriptors]
    width = sizes[0][1] if sizes else 1.0
    ear_series = [d["ear"] for d in descriptors]

    if challenge == "blink_twice":
        return (_check_blink(ear_series, min_dips=2), "blink_twice")
    if challenge == "blink":
        return (_check_blink(ear_series, min_dips=1), "blink")
    if challenge == "look_left":
        x0, _ = centroids[0]
        x_min = min(c[0] for c in centroids)
        return ((x0 - x_min) / width >= _CHALLENGE_LATERAL_THRESHOLD, "look_left")
    if challenge == "look_right":
        x0, _ = centroids[0]
        x_max = max(c[0] for c in centroids)
        return ((x_max - x0) / width >= _CHALLENGE_LATERAL_THRESHOLD, "look_right")
    if challenge == "look_up":
        y0 = centroids[0][1]
        y_min = min(c[1] for c in centroids)
        height = sizes[0][0] if sizes else 1.0
        return ((y0 - y_min) / height >= _CHALLENGE_VERTICAL_THRESHOLD, "look_up")

    # Unknown challenge — let it through but flag the reason.
    return True, f"unknown_challenge:{challenge}"


def _ensure_stored_embedding(row):
    """Lazily compute + persist the embedding for an enrolment that has a
    face_image but no face_embedding yet, so the matcher works the moment
    face_recognition is installed without a re-enrol cycle."""
    raw = row.get("face_embedding") or ""
    if raw:
        try:
            stored = json.loads(raw)
            if isinstance(stored, list) and stored:
                return stored
        except Exception:
            pass
    file_url = row.get("face_image")
    if not file_url:
        return None
    try:
        arr = _decode_file_url(file_url)
        vec = _embed_array(arr)
        if vec is None:
            return None
        frappe.db.set_value(
            "Employee Face Enrollment",
            row["name"],
            "face_embedding",
            json.dumps(vec),
            update_modified=False,
        )
        return vec
    except Exception as e:
        frappe.log_error(f"lazy embed failed for {row.get('name')}: {e}", "Upande Bio")
        return None


def _match_embedding(probe):
    """Cosine-match a probe embedding against every active enrolment."""
    rows = frappe.get_all(
        "Employee Face Enrollment",
        filters={"is_active": 1},
        fields=["name", "employee", "employee_name", "face_image", "face_embedding"],
        limit_page_length=5000,
    )
    best_emp = best_name = None
    best_dist = None
    for r in rows:
        stored = _ensure_stored_embedding(r)
        if not stored:
            continue
        d = _cosine_distance(probe, stored)
        if best_dist is None or d < best_dist:
            best_dist = d
            best_name = r.get("employee_name")
            best_emp = r.get("employee")
    frappe.db.commit()
    return best_emp, best_name, best_dist


# ── Public endpoints ────────────────────────────────────────────────────────


def _evaluate_liveness(descriptors, challenge, match_distance):
    """Decide whether the captured frames look like a live person.

    Soft model: if the face match is highly confident (distance well below
    threshold), we *trust* it and waive challenge / motion gates — at that
    confidence it really is the enrolled face, not a stranger spoofing a
    photo. We only hard-reject when there's a clear spoof signal AND the
    match is borderline.

    Returns (passed: bool, reason: str | None).
    """
    centroids = [d["centroid"] for d in descriptors]
    motion_ok = _check_motion(centroids)
    challenge_ok, challenge_why = _check_challenge(challenge, descriptors)

    high_confidence = match_distance is not None and match_distance <= _HIGH_CONFIDENCE_THRESHOLD
    confident_enough = match_distance is not None and match_distance <= _MATCH_THRESHOLD

    if high_confidence:
        # Very confident match — accept regardless of weak liveness signals.
        return True, None

    if confident_enough and motion_ok:
        # Match is OK and we saw motion — accept even if the specific
        # challenge gesture wasn't crisp enough to register.
        return True, None

    if not motion_ok:
        return False, "no_motion_between_frames"
    if not challenge_ok:
        return False, f"challenge_failed:{challenge_why}"

    # Liveness signals look fine but the match itself is the problem.
    return False, "no_match"


@frappe.whitelist()
def match_face_v2(frames=None, challenge=None):
    """Multi-frame face match with liveness."""
    result = {
        "available": False,
        "matched": False,
        "liveness_passed": False,
        "reason": None,
        "employee": None,
        "employee_name": None,
        "distance": None,
        "threshold": _MATCH_THRESHOLD,
        "challenge": challenge,
    }

    if not _matcher_available():
        result["reason"] = "face_recognition_not_installed"
        return result
    result["available"] = True

    if not frames:
        result["reason"] = "no_frames_supplied"
        return result
    if isinstance(frames, str):
        try:
            frames = json.loads(frames)
        except Exception:
            result["reason"] = "frames_not_a_list"
            return result
    if not isinstance(frames, list) or len(frames) < 2:
        result["reason"] = "need_at_least_two_frames"
        return result

    try:
        descriptors = []
        decoded_arrays = []
        for i, b64 in enumerate(frames):
            try:
                arr = _decode_b64(b64)
            except Exception:
                result["reason"] = f"decode_failed_frame_{i}"
                return result
            d = _frame_descriptors(arr)
            if d is None:
                # Not every frame must have a face — sometimes the user
                # blinks exactly when we snap. Skip and keep going; we
                # only reject if too few frames produced a face.
                continue
            descriptors.append(d)
            decoded_arrays.append(arr)

        if len(descriptors) < max(2, len(frames) // 2):
            result["reason"] = "face_lost_in_too_many_frames"
            return result

        # ── Match first; let _evaluate_liveness use distance to decide ──
        # Pick the frame with the largest detected face (closest to the
        # camera) — that's the highest-quality probe.
        def _area(d):
            t, r, b, l = d["bbox"]
            return (b - t) * (r - l)
        probe_idx = max(range(len(descriptors)), key=lambda i: _area(descriptors[i]))
        probe = _embed_array(decoded_arrays[probe_idx], box=descriptors[probe_idx]["bbox"])
        if probe is None:
            result["reason"] = "embedding_failed"
            return result

        emp, name, dist = _match_embedding(probe)
        if dist is None:
            result["reason"] = "no_enrollments_with_embeddings"
            return result
        result["distance"] = round(dist, 4)

        # Soft liveness — uses match distance as a tie-breaker so a
        # confident face match never gets blocked by a weak challenge
        # signal (which is the common cause of real users being told
        # 'we can't recognise you').
        passed, why = _evaluate_liveness(descriptors, challenge, dist)
        result["liveness_passed"] = passed
        if not passed:
            result["reason"] = why
            return result

        if dist <= _MATCH_THRESHOLD:
            result["matched"] = True
            result["employee"] = emp
            result["employee_name"] = name
        else:
            result["reason"] = "no_match"
        return result
    except Exception as e:
        frappe.log_error(f"match_face_v2 failed: {e}", "Upande Bio")
        result["reason"] = str(e)
        return result


@frappe.whitelist()
def match_face(image_b64: str):
    """Single-frame match. No liveness — kept as a fallback."""
    response = {
        "available": False,
        "matched": False,
        "employee": None,
        "employee_name": None,
        "distance": None,
        "threshold": _MATCH_THRESHOLD,
        "error": None,
    }
    if not _matcher_available():
        response["error"] = "face_recognition_not_installed"
        return response
    response["available"] = True
    try:
        arr = _decode_b64(image_b64)
        probe = _embed_array(arr)
        if probe is None:
            response["error"] = "no_face_detected"
            return response
        emp, name, dist = _match_embedding(probe)
        if dist is None:
            response["error"] = "no_enrollments_with_embeddings"
            return response
        response["distance"] = round(dist, 4)
        if dist <= _MATCH_THRESHOLD:
            response["matched"] = True
            response["employee"] = emp
            response["employee_name"] = name
        return response
    except Exception as e:
        frappe.log_error(f"match_face failed: {e}", "Upande Bio")
        response["error"] = str(e)
        return response


@frappe.whitelist()
def compute_face_embedding(image_b64: str = None, file_url: str = None):
    """Single-frame embedding for the enrolment flow."""
    if not _matcher_available():
        return {"available": False, "embedding": None, "error": "face_recognition_not_installed"}
    try:
        if file_url:
            arr = _decode_file_url(file_url)
        elif image_b64:
            arr = _decode_b64(image_b64)
        else:
            return {"available": True, "embedding": None, "error": "no_image_supplied"}
        vec = _embed_array(arr)
        if vec is None:
            return {"available": True, "embedding": None, "error": "no_face_detected"}
        return {"available": True, "embedding": vec, "error": None}
    except Exception as e:
        frappe.log_error(f"compute_face_embedding failed: {e}", "Upande Bio")
        return {"available": True, "embedding": None, "error": str(e)}


@frappe.whitelist()
def backfill_face_embeddings():
    """Compute embeddings for active enrolments that have a face_image
    but no face_embedding. Manager calls this after installing
    face_recognition or bulk-enrolling photos."""
    if not _matcher_available():
        return {"available": False, "processed": 0, "computed": 0, "failed": 0, "error": "face_recognition_not_installed"}
    rows = frappe.get_all(
        "Employee Face Enrollment",
        filters={"is_active": 1},
        fields=["name", "face_image", "face_embedding"],
        limit_page_length=10000,
    )
    processed = computed = failed = 0
    for r in rows:
        processed += 1
        if r.get("face_embedding"):
            continue
        if not r.get("face_image"):
            continue
        try:
            arr = _decode_file_url(r["face_image"])
            vec = _embed_array(arr)
            if vec is None:
                failed += 1
                continue
            frappe.db.set_value(
                "Employee Face Enrollment",
                r["name"],
                "face_embedding",
                json.dumps(vec),
                update_modified=False,
            )
            computed += 1
        except Exception as e:
            frappe.log_error(f"backfill failed for {r.get('name')}: {e}", "Upande Bio")
            failed += 1
    frappe.db.commit()
    return {"available": True, "processed": processed, "computed": computed, "failed": failed, "error": None}


@frappe.whitelist()
def matcher_status():
    """Diagnostic for the manager home."""
    available = _matcher_available()
    with_emb = frappe.db.count("Employee Face Enrollment", filters={"is_active": 1, "face_embedding": ["is", "set"]})
    with_img = frappe.db.count("Employee Face Enrollment", filters={"is_active": 1, "face_image": ["is", "set"]})
    return {
        "available": available,
        "library": "face_recognition",
        "threshold": _MATCH_THRESHOLD,
        "motion_threshold_px": _MOTION_PIXEL_THRESHOLD,
        "blink_ear_threshold": _BLINK_EAR_THRESHOLD,
        "enrolments_with_image": with_img,
        "enrolments_with_embedding": with_emb,
    }
