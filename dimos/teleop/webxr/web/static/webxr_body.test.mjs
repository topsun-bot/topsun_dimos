import assert from "node:assert/strict";
import { test } from "node:test";

import { captureBody } from "./webxr_body.mjs";

function pose(x) {
    return {
        transform: {
            position: { x, y: 0, z: 0 },
            orientation: { x: 0, y: 0, z: 0, w: 1 },
        },
    };
}

test("missing body is null", () => {
    assert.equal(captureBody({ body: null }, "ref"), null);
});

test("iterable XRBody uses getPose", () => {
    const body = [["hips", { id: "hips-space" }]];
    const frame = {
        body,
        getPose(space) {
            return space.id === "hips-space" ? pose(1.5) : null;
        },
    };
    assert.deepEqual(captureBody(frame, "ref"), {
        hips: { position: [1.5, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("joints array of spaces uses jointName", () => {
    const frame = {
        body: { joints: [{ jointName: "chest", id: "chest-space" }] },
        getPose(space) {
            return space.id === "chest-space" ? pose(2) : null;
        },
    };
    assert.deepEqual(captureBody(frame, "ref"), {
        chest: { position: [2, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("joints array of wrappers uses jointSpace", () => {
    const frame = {
        body: {
            joints: [{ jointName: "neck", jointSpace: { id: "neck-space" } }],
        },
        getPose(space) {
            return space.id === "neck-space" ? pose(3) : null;
        },
    };
    assert.deepEqual(captureBody(frame, "ref"), {
        neck: { position: [3, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("getJointPose throw falls back to getPose", () => {
    const body = [["hips", { id: "hips-space" }]];
    const frame = {
        body,
        getJointPose() {
            throw new TypeError("not an XRJointSpace");
        },
        getPose(space) {
            return space.id === "hips-space" ? pose(4) : null;
        },
    };
    assert.deepEqual(captureBody(frame, "ref"), {
        hips: { position: [4, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("present body with no poses is empty", () => {
    const frame = {
        body: [["hips", { id: "missing" }]],
        getPose() {
            return null;
        },
    };
    assert.deepEqual(captureBody(frame, "ref"), {});
});
