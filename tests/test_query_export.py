import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from main import build_parser
from src.anchors import (
    inspect_text_path,
    load_timeline,
    prepare_book_timeline,
    source_units_path,
)
from src.calibre_native import save_manifest
from src.query_export import (
    DEFAULT_PROMPT_VERSION,
    FakeQueryGenerator,
    OllamaQueryGenerator,
    append_query_record,
    build_batch_query_spans,
    build_generated_query_record,
    build_query_record,
    build_query_span,
    drift_warnings_for_export,
    generate_query_records,
    build_generation_prompt,
    load_text_blocks_for_export,
    query_records_path,
    validate_query_record,
)


def fake_book(tmp_path, calibre_book_id=42):
    epub = tmp_path / f"book-{calibre_book_id}.epub"
    epub.write_bytes(b"fake epub")
    return {
        "calibre_book_id": calibre_book_id,
        "calibre_uuid": f"uuid-{calibre_book_id}",
        "title": "Book",
        "authors": ["Author"],
        "library_path": str(tmp_path),
        "calibre_path": "Author/Book",
        "formats": {"EPUB": str(epub)},
        "preferred_epub_path": str(epub),
        "annots_key": "abc.json",
        "prepared": True,
    }


def fake_spine():
    return [
        {
            "spine_index": 0,
            "href": "chapter.xhtml",
            "text": "\n\n".join(
                [
                    "A cold quiet room waits beneath the house.",
                    "The family whispers while distant thunder grows.",
                    "A locked door rattles once and then falls still.",
                    "Someone descends the stairs with a covered lamp.",
                ]
            ),
        },
        {
            "spine_index": 1,
            "href": "next.xhtml",
            "text": "Bright morning returns.",
        },
    ]


def prepare_fake_book(tmp_path, debug_text=True):
    book = fake_book(tmp_path)

    def extractor(epub_path):
        assert epub_path == book["preferred_epub_path"]
        return fake_spine()

    data_dir = tmp_path / "data"
    prepare_book_timeline(
        book,
        data_dir=data_dir,
        target_words=8,
        min_words=4,
        debug_text=debug_text,
        spine_extractor=extractor,
    )
    return book, data_dir


def write_needs_query_file(tmp_path, max_spans=1):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    input_path = tmp_path / "query_records.needs_query.jsonl"
    for span in build_batch_query_spans(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        max_spans=max_spans,
        target_words=14,
        min_words=8,
        max_words=20,
    ):
        source = build_query_record(
            book=book,
            timeline=timeline,
            span=span,
            query_text="",
            query_mode="needs_query",
            generation_method="batch_placeholder_v1",
            review_status="needs_query",
            allow_empty_query=True,
        )
        append_query_record(input_path, source)
    return book, data_dir, input_path


def start_fake_ollama_server(*, status=200, response_text="tense sparse strings; slow pulse; no vocals"):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            requests.append({"path": self.path, "body": json.loads(body)})
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if status == 200:
                payload = {"model": "qwen3:4b", "response": response_text, "done": True}
            else:
                payload = {"error": "model unavailable"}
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return server, base_url, requests


def test_load_text_blocks_uses_debug_text_sidecar(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path, debug_text=True)
    timeline = load_timeline(data_dir, book["calibre_book_id"])

    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)

    assert blocks[0]["unit_id"] == 0
    assert blocks[0]["_text"] == "A cold quiet room waits beneath the house."
    assert source_units_path(data_dir, book["calibre_book_id"]).exists()
    assert inspect_text_path(data_dir, book["calibre_book_id"]).exists()


def test_load_text_blocks_can_rehydrate_from_epub_extraction(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path, debug_text=False)
    timeline = load_timeline(data_dir, book["calibre_book_id"])

    blocks = load_text_blocks_for_export(
        data_dir,
        book,
        timeline=timeline,
        spine_extractor=lambda epub_path: fake_spine(),
    )

    assert blocks[1]["_text"] == "The family whispers while distant thunder grows."


def test_build_query_span_expands_adjacent_text_blocks_within_spine(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)

    span = build_query_span(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        spine_index=0,
        local_char_offset=45,
        source_cfi="epubcfi(/6/2:45)",
        target_words=18,
        min_words=12,
        max_words=25,
    )

    assert span["span_id"] == "book-42-spine-0-tb-0-3"
    assert span["text_block_start"] == 0
    assert span["text_block_end"] == 3
    assert "distant thunder" in span["excerpt"]
    assert span["source_cfi"] == "epubcfi(/6/2:45)"
    assert span["boundary_out"]["text_block_id"] == 3


def test_query_record_is_schema_valid_and_stable(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    span = build_query_span(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        spine_index=0,
        local_char_offset=10,
        target_words=12,
        min_words=8,
        max_words=20,
    )

    record = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="dark quiet instrumental suspense; low strings; no vocals",
    )
    again = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="dark quiet instrumental suspense; low strings; no vocals",
    )

    assert validate_query_record(record) == []
    assert record == again
    assert record["handoff"]["target"] == "music-retrieval-lab"
    assert record["query"]["generation_method"] == "manual_v1"
    assert record["query"]["source"] == "user"


def test_query_record_rejects_out_of_contract_review_state_and_fields(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    span = build_query_span(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        spine_index=0,
        local_char_offset=10,
        target_words=12,
        min_words=8,
        max_words=20,
    )
    record = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="dark quiet instrumental suspense; low strings; no vocals",
    )
    record["review"]["status"] = "selected"
    record["retrieval_results"] = {"candidates": [{"asset_id": "asset-1"}]}

    errors = validate_query_record(record)

    assert "review_status_invalid" in errors
    assert "forbidden_query_record_field:$.retrieval_results" in errors
    assert "forbidden_query_record_field:$.retrieval_results.candidates" in errors


def test_batch_query_spans_cover_blocks_without_overlap(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)

    spans = build_batch_query_spans(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        target_words=14,
        min_words=8,
        max_words=20,
    )

    covered = []
    for span in spans:
        assert span["selection_method"] == "batch_text_blocks_v1"
        assert span["source_cfi"] is None
        assert span["text_block_start"] < span["text_block_end"]
        covered.extend(range(span["text_block_start"], span["text_block_end"]))

    assert covered == [0, 1, 2, 3, 4]
    assert len(covered) == len(set(covered))
    assert spans[0]["spine_index"] == 0
    assert spans[-1]["spine_index"] == 1


def test_empty_needs_query_record_is_schema_valid(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    span = build_batch_query_spans(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        max_spans=1,
        target_words=14,
        min_words=8,
        max_words=20,
    )[0]

    record = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="",
        query_mode="needs_query",
        generation_method="batch_placeholder_v1",
        review_status="needs_query",
        allow_empty_query=True,
    )

    assert validate_query_record(record) == []
    assert record["record_id"].endswith("-q-needs-query")
    assert record["query"]["text"] == ""
    assert record["review"]["status"] == "needs_query"


def test_generated_query_record_preserves_span_and_adds_generation_metadata(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    source = build_query_record(
        book=book,
        timeline=timeline,
        span=build_batch_query_spans(
            book=book,
            timeline=timeline,
            text_blocks=blocks,
            max_spans=1,
            target_words=14,
            min_words=8,
            max_words=20,
        )[0],
        query_text="",
        query_mode="needs_query",
        generation_method="batch_placeholder_v1",
        review_status="needs_query",
        allow_empty_query=True,
    )

    record = build_generated_query_record(
        source_record=source,
        query_text="quiet ominous instrumental suspense; low strings; no vocals",
        generation_method="local_model_audio_intent_v1",
        model="fake-audio-intent-v1",
        prompt_version="audio_intent_v1",
        provider="fake",
    )

    assert validate_query_record(record) == []
    assert record["span"] == source["span"]
    assert record["query"]["mode"] == "generated"
    assert record["query"]["model"] == "fake-audio-intent-v1"
    assert record["query"]["provider"] == "fake"
    assert record["query"]["prompt_version"] == "audio_intent_v1"
    assert record["query"]["input_excerpt_hash"] == source["span"]["excerpt_hash"]
    assert record["review"]["status"] == "unreviewed"
    assert source["query"]["mode"] == "needs_query"


def test_generation_prompt_matches_manual_audio_intent_shape(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    source = build_query_record(
        book=book,
        timeline=timeline,
        span=build_batch_query_spans(
            book=book,
            timeline=timeline,
            text_blocks=blocks,
            max_spans=1,
            target_words=14,
            min_words=8,
            max_words=20,
        )[0],
        query_text="",
        query_mode="needs_query",
        generation_method="batch_placeholder_v1",
        review_status="needs_query",
        allow_empty_query=True,
    )

    prompt = build_generation_prompt(source, "audio_intent_v1")

    assert "Write one background-music or ambience search query" in prompt
    assert "do not copy examples, choose from a menu" in prompt
    assert "Replace character names and proper nouns with roles or situations." in prompt
    assert "Return only the query phrase." in prompt


def test_generation_prompt_scene_version_is_scene_first(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    source = build_query_record(
        book=book,
        timeline=timeline,
        span=build_batch_query_spans(
            book=book,
            timeline=timeline,
            text_blocks=blocks,
            max_spans=1,
            target_words=14,
            min_words=8,
            max_words=20,
        )[0],
        query_text="",
        query_mode="needs_query",
        generation_method="batch_placeholder_v1",
        review_status="needs_query",
        allow_empty_query=True,
    )

    prompt = build_generation_prompt(source, DEFAULT_PROMPT_VERSION)

    assert "You write search phrases for an audio embedding model." in prompt
    assert "what is physically happening" in prompt
    assert "Style examples:" not in prompt
    assert "[scene/action], [setting/object texture], [social or emotional pressure]" in prompt
    assert "Return only one short comma-separated phrase." in prompt


def test_generation_prompt_sparse_version_is_comparison_option(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    source = build_query_record(
        book=book,
        timeline=timeline,
        span=build_batch_query_spans(
            book=book,
            timeline=timeline,
            text_blocks=blocks,
            max_spans=1,
            target_words=14,
            min_words=8,
            max_words=20,
        )[0],
        query_text="",
        query_mode="needs_query",
        generation_method="batch_placeholder_v1",
        review_status="needs_query",
        allow_empty_query=True,
    )

    prompt = build_generation_prompt(source, "audio_intent_sparse_v1")

    assert "# Audio Intent Query Writer" in prompt
    assert "## Internal Checklist" in prompt
    assert "Did you avoid copying any instruction wording into the answer?" in prompt
    assert "Infer the larger scene function they support." in prompt


def test_generate_queries_writes_generated_records_and_cache(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    input_path = tmp_path / "query_records.needs_query.jsonl"
    output = tmp_path / "query_records.generated.jsonl"
    span = build_batch_query_spans(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        max_spans=1,
        target_words=14,
        min_words=8,
        max_words=20,
    )[0]
    source = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="",
        query_mode="needs_query",
        generation_method="batch_placeholder_v1",
        review_status="needs_query",
        allow_empty_query=True,
    )
    append_query_record(input_path, source)

    summary = generate_query_records(
        input_path=input_path,
        output_path=output,
        generator=FakeQueryGenerator(),
        prompt_version="audio_intent_v1",
        overwrite=True,
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["generated_count"] == 1
    assert summary["failed_count"] == 0
    assert len(records) == 1
    assert records[0]["query"]["mode"] == "generated"
    assert records[0]["query"]["source"] == "span_excerpt"
    assert records[0]["query"]["model"] == "fake-audio-intent-v1"
    assert "instrumental audio intent" in records[0]["query"]["text"]
    assert (tmp_path / "query_records.generated.jsonl.cache.json").exists()


def test_generate_queries_cli_ollama_posts_request_and_uses_cache(tmp_path, capsys):
    _, _, input_path = write_needs_query_file(tmp_path)
    output = tmp_path / "query_records.generated.jsonl"
    server, base_url, requests = start_fake_ollama_server()
    parser = build_parser()

    try:
        args = parser.parse_args(
            [
                "generate-queries",
                "--input",
                str(input_path),
                "--out",
                str(output),
                "--provider",
                "ollama",
                "--ollama-url",
                base_url,
                "--model",
                "qwen3:4b-instruct",
                "--timeout",
                "2",
                "--temperature",
                "0.15",
                "--num-predict",
                "32",
                "--keep-alive",
                "5m",
                "--prompt-version",
                "audio_intent_v1",
                "--overwrite",
            ]
        )
        assert args.func(args) == 0
        first = capsys.readouterr()

        args = parser.parse_args(
            [
                "generate-queries",
                "--input",
                str(input_path),
                "--out",
                str(output),
                "--provider",
                "ollama",
                "--ollama-url",
                base_url,
                "--model",
                "qwen3:4b-instruct",
                "--prompt-version",
                "audio_intent_v1",
                "--overwrite",
            ]
        )
        assert args.func(args) == 0
        second = capsys.readouterr()
    finally:
        server.shutdown()
        server.server_close()

    assert len(requests) == 1
    payload = requests[0]["body"]
    assert requests[0]["path"] == "/api/generate"
    assert payload["model"] == "qwen3:4b-instruct"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0.15, "num_predict": 32}
    assert payload["keep_alive"] == "5m"
    assert "do not copy examples, choose from a menu" in payload["prompt"]
    assert "Replace character names and proper nouns with roles or situations." in payload["prompt"]
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["query"]["provider"] == "ollama"
    assert records[0]["query"]["model"] == "qwen3:4b-instruct"
    assert records[0]["query"]["text"] == "tense sparse strings; slow pulse; no vocals"
    assert "Generated query records: 1" in first.out
    assert "Cache hits: 1" in second.out


def test_generate_queries_cli_defaults_to_scene_prompt(tmp_path):
    _, _, input_path = write_needs_query_file(tmp_path)
    output = tmp_path / "query_records.generated.jsonl"
    server, base_url, requests = start_fake_ollama_server()
    parser = build_parser()

    try:
        args = parser.parse_args(
            [
                "generate-queries",
                "--input",
                str(input_path),
                "--out",
                str(output),
                "--provider",
                "ollama",
                "--ollama-url",
                base_url,
                "--model",
                "qwen3:4b-instruct",
                "--overwrite",
            ]
        )
        assert args.func(args) == 0
    finally:
        server.shutdown()
        server.server_close()

    assert requests
    assert f"Prompt version: {DEFAULT_PROMPT_VERSION}" in requests[0]["body"]["prompt"]
    assert "You write search phrases for an audio embedding model." in requests[0]["body"]["prompt"]
    assert "Style examples:" not in requests[0]["body"]["prompt"]
    assert "[scene/action], [setting/object texture], [social or emotional pressure]" in requests[0]["body"]["prompt"]
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert records[0]["query"]["prompt_version"] == DEFAULT_PROMPT_VERSION


def test_ollama_generator_failure_is_written_to_error_sidecar(tmp_path):
    _, _, input_path = write_needs_query_file(tmp_path)
    output = tmp_path / "query_records.generated.jsonl"
    errors = tmp_path / "query_records.generated.errors.jsonl"
    server, base_url, requests = start_fake_ollama_server(status=500)

    try:
        summary = generate_query_records(
            input_path=input_path,
            output_path=output,
            generator=OllamaQueryGenerator(base_url=base_url, model_id="qwen3:4b", timeout_seconds=2),
            prompt_version="audio_intent_v1",
            errors_path=errors,
            overwrite=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    error_record = json.loads(errors.read_text(encoding="utf-8").strip())
    assert len(requests) == 1
    assert summary["generated_count"] == 0
    assert summary["failed_count"] == 1
    assert output.read_text(encoding="utf-8") == ""
    assert "Ollama request failed" in error_record["error"]
    assert base_url in error_record["error"]
    assert "qwen3:4b" in error_record["error"]


def test_generate_queries_records_failures_in_sidecar(tmp_path):
    class FailingGenerator:
        provider = "fake"
        model_id = "failing-model"

        def generate(self, record, prompt):
            raise RuntimeError("model unavailable")

    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    blocks = load_text_blocks_for_export(data_dir, book, timeline=timeline)
    input_path = tmp_path / "query_records.needs_query.jsonl"
    output = tmp_path / "query_records.generated.jsonl"
    errors = tmp_path / "query_records.generated.errors.jsonl"
    span = build_batch_query_spans(
        book=book,
        timeline=timeline,
        text_blocks=blocks,
        max_spans=1,
        target_words=14,
        min_words=8,
        max_words=20,
    )[0]
    source = build_query_record(
        book=book,
        timeline=timeline,
        span=span,
        query_text="",
        query_mode="needs_query",
        generation_method="batch_placeholder_v1",
        review_status="needs_query",
        allow_empty_query=True,
    )
    append_query_record(input_path, source)

    summary = generate_query_records(
        input_path=input_path,
        output_path=output,
        generator=FailingGenerator(),
        prompt_version="audio_intent_v1",
        errors_path=errors,
        overwrite=True,
    )

    error_record = json.loads(errors.read_text(encoding="utf-8").strip())
    assert summary["generated_count"] == 0
    assert summary["failed_count"] == 1
    assert output.read_text(encoding="utf-8") == ""
    assert error_record["record_id"] == source["record_id"]
    assert error_record["error"] == "model unavailable"


def test_drift_warnings_are_exposed_for_export(tmp_path):
    book, data_dir = prepare_fake_book(tmp_path)
    timeline = load_timeline(data_dir, book["calibre_book_id"])
    book["annots_key"] = "changed.json"

    warnings = drift_warnings_for_export(book, timeline)

    assert any(w.startswith("annots_key_changed") for w in warnings)


def test_export_query_cli_writes_one_jsonl_record(tmp_path, capsys):
    book, data_dir = prepare_fake_book(tmp_path)
    manifest = {
        "libraries": [str(tmp_path)],
        "books": [book],
    }
    save_manifest(manifest, data_dir=data_dir)
    output = tmp_path / "queries.jsonl"
    parser = build_parser()
    args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "export-query",
            "42",
            "--spine-index",
            "0",
            "--local-char-offset",
            "20",
            "--query-text",
            "subdued ominous instrumental tension; no vocals",
            "--target-words",
            "12",
            "--min-words",
            "8",
            "--max-words",
            "18",
            "--output",
            str(output),
        ]
    )

    assert args.func(args) == 0

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == 1
    assert record["book"]["calibre_book_id"] == 42
    assert record["span"]["selection_method"] == "manual_resolved_position_v1"
    assert record["query"]["text"] == "subdued ominous instrumental tension; no vocals"
    assert query_records_path(data_dir, 42).parent.exists()
    captured = capsys.readouterr()
    assert "Exported query record:" in captured.out


def test_export_batch_spans_cli_writes_needs_query_records(tmp_path, capsys):
    book, data_dir = prepare_fake_book(tmp_path)
    manifest = {
        "libraries": [str(tmp_path)],
        "books": [book],
    }
    save_manifest(manifest, data_dir=data_dir)
    output = tmp_path / "batch_queries.jsonl"
    parser = build_parser()
    args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "export-batch-spans",
            "42",
            "--target-words",
            "14",
            "--min-words",
            "8",
            "--max-words",
            "20",
            "--max-spans",
            "2",
            "--output",
            str(output),
        ]
    )

    assert args.func(args) == 0

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["query"]["mode"] == "needs_query"
    assert records[0]["query"]["text"] == ""
    assert records[0]["query"]["generation_method"] == "batch_placeholder_v1"
    assert records[0]["review"]["status"] == "needs_query"
    assert records[0]["span"]["selection_method"] == "batch_text_blocks_v1"
    captured = capsys.readouterr()
    assert "Exported batch query span records: 2" in captured.out


def test_generate_queries_cli_writes_generated_records(tmp_path, capsys):
    book, data_dir = prepare_fake_book(tmp_path)
    manifest = {
        "libraries": [str(tmp_path)],
        "books": [book],
    }
    save_manifest(manifest, data_dir=data_dir)
    input_path = tmp_path / "batch_queries.jsonl"
    output = tmp_path / "generated_queries.jsonl"
    parser = build_parser()
    batch_args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "export-batch-spans",
            "42",
            "--target-words",
            "14",
            "--min-words",
            "8",
            "--max-words",
            "20",
            "--max-spans",
            "1",
            "--output",
            str(input_path),
        ]
    )
    assert batch_args.func(batch_args) == 0

    args = parser.parse_args(
        [
            "--data-dir",
            str(data_dir),
            "generate-queries",
            "--input",
            str(input_path),
            "--out",
            str(output),
            "--provider",
            "fake",
        ]
    )

    assert args.func(args) == 0

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["query"]["mode"] == "generated"
    assert records[0]["review"]["status"] == "unreviewed"
    captured = capsys.readouterr()
    assert "Generated query records: 1" in captured.out
