load("@python_deps//:requirements.bzl", "requirement")

py_binary(
    name = "webapp",
    srcs = [
        "app/processing.py",
        "app/util.py",
        "app/webapp.py",
    ] + glob(["app/blueprints/*.py"]),
    data = glob([
        "app/templates/*",
        "app/static/*",
    ]),
    main = "app/webapp.py",
    deps = [
        ":brivo",
        requirement("requests"),
        requirement("flask"),
        requirement("flask_session"),
        requirement("msgspec"),
        requirement("asgiref"),
    ],
    imports = ["."],
)

py_library(
    name = "brivo",
    srcs = [
        "app/brivo.py",
    ],
    deps = [
        ":brivo_errors",
        ":util",
        requirement("requests"),
        requirement("async-lru"),
        requirement("aiohttp"),
        "//common:timer",
    ],
)

py_library(
    name = "brivo_errors",
    srcs = ["app/brivo_errors.py"],
)

py_library(
    name = "util",
    srcs = ["app/util.py"],
    deps = [
        ":brivo_errors",
        requirement("flask"),
    ],
)

py_library(
    name = "webapp_test_base",
    srcs = [
        "tests/fixtures.py",
        "tests/webapp_base.py",
    ],
    deps = [
        requirement("mock"),
        requirement("lxml"),
    ],
)

py_test(
    name = "webapp_test",
    size = "small",
    srcs = [
        "tests/webapp_test.py",
    ],
    deps = [
        ":webapp",
        ":webapp_test_base",
    ],
)

py_test(
    name = "webapp_oauth_test",
    size = "small",
    srcs = [
        "tests/webapp_oauth_test.py",
    ],
    deps = [
        ":webapp",
        ":webapp_test_base",
    ],
)

py_test(
    name = "webapp_upload_test",
    size = "small",
    srcs = [
        "tests/webapp_upload_test.py",
    ],
    deps = [
        ":webapp",
        ":webapp_test_base",
    ],
)

py_test(
    name = "brivo_test",
    size = "small",
    srcs = ["tests/brivo_test.py"],
    deps = [
        ":brivo",
    ],
    imports = ["."],
)
