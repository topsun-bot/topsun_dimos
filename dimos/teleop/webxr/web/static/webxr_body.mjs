// Capture every body-joint pose that resolves in this animation frame.
// A missing body source is different from a present source with no usable poses.
export function captureBody(frame, referenceSpace) {
    const body = frame.body;
    if (!body) return null;

    const joints = {};
    for (const [jointName, jointSpace] of bodyEntries(body)) {
        if (!jointName || !jointSpace) continue;
        const pose = resolveBodyPose(frame, jointSpace, referenceSpace);
        if (!pose) continue;

        const position = pose.transform.position;
        const orientation = pose.transform.orientation;
        joints[jointName] = {
            position: [position.x, position.y, position.z],
            orientation: [orientation.x, orientation.y, orientation.z, orientation.w],
        };
    }
    return joints;
}

// Spec XRBody is iterable<XRBodyJoint, XRBodySpace>. Some runtimes instead
// expose a joints array of spaces with jointName set.
function bodyEntries(body) {
    if (typeof body[Symbol.iterator] === "function") {
        return body;
    }
    return (body.joints ?? []).map((entry) => {
        const space = entry.jointSpace ?? entry;
        return [entry.jointName ?? space.jointName, space];
    });
}

// XRBodySpace is an XRSpace, so getPose is the spec method. getJointPose is
// for XRJointSpace (hands); try it first when present, then fall back.
function resolveBodyPose(frame, jointSpace, referenceSpace) {
    if (typeof frame.getJointPose === "function") {
        try {
            const pose = frame.getJointPose(jointSpace, referenceSpace);
            if (pose) return pose;
        } catch {
            // XRBodySpace is not an XRJointSpace on spec-compliant runtimes.
        }
    }
    return frame.getPose(jointSpace, referenceSpace);
}
