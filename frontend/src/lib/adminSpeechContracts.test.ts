import { describe, expect, it } from "vitest";

import {
  parseAdminSpeechManifest,
  parseAdminSpeechPublishJob,
} from "./adminSpeechContracts";
import { ApiContractError } from "./apiTransport";

describe("admin API contracts", () => {
  it("accepts a manifest without retired dialogue metadata", () => {
    expect(
      parseAdminSpeechManifest({
        generatedAt: "2026-07-09T00:00:00Z",
        mode: "local",
        files: [],
        sync: {
          status: "disabled",
          message: "off",
          serverCommit: null,
          updatedAt: "2026-07-09T00:00:00Z",
        },
        deploy: { enabled: false, message: "off" },
      }),
    ).toMatchObject({ mode: "local", files: [] });
  });

  it("accepts a publish job with nullable lifecycle fields", () => {
    expect(
      parseAdminSpeechPublishJob({
        id: "publish-1",
        status: "pending",
        createdAt: "2026-07-09T00:00:00Z",
        startedAt: null,
        finishedAt: null,
        logs: [],
        error: null,
        errorCode: null,
        savedFiles: [],
        commitSha: null,
      }),
    ).toMatchObject({ id: "publish-1", status: "pending" });
  });

  it("rejects a manifest with an incomplete file entry", () => {
    expect(() =>
      parseAdminSpeechManifest({
        generatedAt: "2026-07-09T00:00:00Z",
        mode: "local",
        files: [{ id: "speech_runtime" }],
        sync: {
          status: "disabled",
          message: "off",
          serverCommit: null,
          updatedAt: "2026-07-09T00:00:00Z",
        },
        deploy: { enabled: false, message: "off" },
      }),
    ).toThrow(ApiContractError);
  });
});
