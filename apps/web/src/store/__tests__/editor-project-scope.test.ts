import { beforeEach, describe, expect, it } from "vitest";

import { useInspectorStore } from "@/store/inspector";
import { useStyleEditStore } from "@/store/styleEdit";

describe("MAX editor project scope", () => {
  beforeEach(() => {
    useInspectorStore.setState({
      projectScope: null,
      editorSession: null,
      inspectMode: false,
      selections: [],
    });
    useStyleEditStore.setState({
      projectScope: null,
      editorSession: null,
      styleMode: false,
      selected: null,
      tokens: {},
      elements: {},
      dirty: false,
    });
  });

  it("preserves shared desktop/drawer picks inside one project", () => {
    const inspector = useInspectorStore.getState();
    inspector.scopeToProject("project-a");
    inspector.setInspectMode(true);
    inspector.addSelection({
      id: "desktop|1",
      selector: ".workout-card",
      label: "Тренировка",
      text: null,
      html: null,
      comment: "",
    });

    useInspectorStore.getState().scopeToProject("project-a");

    expect(useInspectorStore.getState()).toMatchObject({
      projectScope: "project-a",
      inspectMode: true,
      selections: [{ selector: ".workout-card" }],
    });
  });

  it("drops AI picks and unsaved styles before another project renders", () => {
    const inspector = useInspectorStore.getState();
    inspector.scopeToProject("project-a");
    inspector.setInspectMode(true);
    inspector.addSelection({
      id: "desktop|1",
      selector: ".private-card",
      label: null,
      text: "A",
      html: null,
      comment: "",
    });

    const style = useStyleEditStore.getState();
    style.scopeToProject("project-a");
    style.setStyleMode(true);
    style.setElementProp(".private-card", "color", "#334455");

    useInspectorStore.getState().scopeToProject("project-b");
    useStyleEditStore.getState().scopeToProject("project-b");

    expect(useInspectorStore.getState()).toMatchObject({
      projectScope: "project-b",
      inspectMode: false,
      selections: [],
    });
    expect(useStyleEditStore.getState()).toMatchObject({
      projectScope: "project-b",
      styleMode: false,
      selected: null,
      elements: {},
      tokens: {},
      dirty: false,
    });
  });

  it("drops edits made in another editor before re-entering the same MAX project", () => {
    useInspectorStore.getState().scopeToProject("project-a");
    useStyleEditStore.getState().scopeToProject("project-a");
    useInspectorStore.getState().releaseProjectScope("project-a");
    useStyleEditStore.getState().releaseProjectScope("project-a");

    // The generic editor does not claim a MAX scope, but it shares these
    // transient stores and may be used between two visits to the same app.
    useInspectorStore.getState().addSelection({
      id: "generic|1",
      selector: ".generic-card",
      label: null,
      text: null,
      html: null,
      comment: "",
    });
    useStyleEditStore
      .getState()
      .setElementProp(".generic-card", "color", "#abcdef");

    useInspectorStore.getState().scopeToProject("project-a");
    useStyleEditStore.getState().scopeToProject("project-a");

    expect(useInspectorStore.getState().selections).toEqual([]);
    expect(useStyleEditStore.getState()).toMatchObject({
      projectScope: "project-a",
      elements: {},
      dirty: false,
    });
  });

  it("resets a same-project remount and ignores the stale instance cleanup", () => {
    useInspectorStore.getState().scopeToProject("project-a", "session-old");
    useStyleEditStore.getState().scopeToProject("project-a", "session-old");
    useInspectorStore.getState().setInspectMode(true);
    useStyleEditStore.getState().setStyleMode(true);

    useInspectorStore.getState().scopeToProject("project-a", "session-new");
    useStyleEditStore.getState().scopeToProject("project-a", "session-new");
    useInspectorStore
      .getState()
      .releaseProjectScope("project-a", "session-old");
    useStyleEditStore
      .getState()
      .releaseProjectScope("project-a", "session-old");

    expect(useInspectorStore.getState()).toMatchObject({
      projectScope: "project-a",
      editorSession: "session-new",
      inspectMode: false,
    });
    expect(useStyleEditStore.getState()).toMatchObject({
      projectScope: "project-a",
      editorSession: "session-new",
      styleMode: false,
    });
  });

  it("stops style picking without closing the selected-element panel", () => {
    const style = useStyleEditStore.getState();
    style.setStyleMode(true);
    style.selectElement({
      selector: "#catalog-tab",
      tag: "button",
      color: "rgb(0, 0, 0)",
      backgroundColor: "rgb(255, 255, 255)",
      borderColor: "rgb(0, 0, 0)",
      fontFamily: "Inter",
    });

    useStyleEditStore.getState().stopStylePicking();

    expect(useStyleEditStore.getState()).toMatchObject({
      styleMode: false,
      selected: { selector: "#catalog-tab" },
    });
  });
});
