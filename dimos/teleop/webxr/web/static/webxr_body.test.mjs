import assert from "node:assert/strict";
import { test } from "node:test";

import { captureBody } from "./webxr_body.mjs";

class FakeXR {
    static pose(x) {
        return {
            transform: {
                position: { x, y: 0, z: 0 },
                orientation: { x: 0, y: 0, z: 0, w: 1 },
            },
        };
    }

    static space(id) {
        return { id };
    }

    static iterableBody(pairs) {
        return pairs;
    }

    static jointsBody(entries) {
        return { joints: entries };
    }

    static frame({ body, getPose, getJointPose }) {
        const frame = { body };
        if (getPose !== undefined) frame.getPose = getPose;
        if (getJointPose !== undefined) frame.getJointPose = getJointPose;
        return frame;
    }
}

test("missing body is null", () => {
    assert.equal(captureBody(FakeXR.frame({ body: null }), "ref"), null);
});

test("undefined body is null", () => {
    assert.equal(captureBody(FakeXR.frame({}), "ref"), null);
});

test("iterable XRBody uses getPose", () => {
    const hips = FakeXR.space("hips-space");
    const frame = FakeXR.frame({
        body: FakeXR.iterableBody([["hips", hips]]),
        getPose(space) {
            return space.id === "hips-space" ? FakeXR.pose(1.5) : null;
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {
        hips: { position: [1.5, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("joints array of spaces uses jointName", () => {
    const chest = { ...FakeXR.space("chest-space"), jointName: "chest" };
    const frame = FakeXR.frame({
        body: FakeXR.jointsBody([chest]),
        getPose(space) {
            return space.id === "chest-space" ? FakeXR.pose(2) : null;
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {
        chest: { position: [2, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("joints array of wrappers uses jointSpace", () => {
    const frame = FakeXR.frame({
        body: FakeXR.jointsBody([
            { jointName: "neck", jointSpace: FakeXR.space("neck-space") },
        ]),
        getPose(space) {
            return space.id === "neck-space" ? FakeXR.pose(3) : null;
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {
        neck: { position: [3, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("getJointPose success does not fall back to getPose", () => {
    const hips = FakeXR.space("hips-space");
    const frame = FakeXR.frame({
        body: FakeXR.iterableBody([["hips", hips]]),
        getJointPose(space) {
            return space.id === "hips-space" ? FakeXR.pose(9) : null;
        },
        getPose() {
            throw new Error("getPose should not run when getJointPose succeeds");
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {
        hips: { position: [9, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("getJointPose throw falls back to getPose", () => {
    const hips = FakeXR.space("hips-space");
    const frame = FakeXR.frame({
        body: FakeXR.iterableBody([["hips", hips]]),
        getJointPose() {
            throw new TypeError("not an XRJointSpace");
        },
        getPose(space) {
            return space.id === "hips-space" ? FakeXR.pose(4) : null;
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {
        hips: { position: [4, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("getJointPose null falls back to getPose", () => {
    const hips = FakeXR.space("hips-space");
    const frame = FakeXR.frame({
        body: FakeXR.iterableBody([["hips", hips]]),
        getJointPose() {
            return null;
        },
        getPose(space) {
            return space.id === "hips-space" ? FakeXR.pose(5) : null;
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {
        hips: { position: [5, 0, 0], orientation: [0, 0, 0, 1] },
    });
});

test("present body with no poses is empty", () => {
    const frame = FakeXR.frame({
        body: FakeXR.iterableBody([["hips", FakeXR.space("missing")]]),
        getPose() {
            return null;
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {});
});

test("empty joints array is empty, not null", () => {
    const frame = FakeXR.frame({
        body: FakeXR.jointsBody([]),
        getPose() {
            return FakeXR.pose(0);
        },
    });
    assert.deepEqual(captureBody(frame, "ref"), {});
});
