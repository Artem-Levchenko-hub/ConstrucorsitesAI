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

  it("drops picks and unsaved styles when another project opens", () => {
    useInspectorStore.getState().scopeToProject("project-a", "session-a");
    useInspectorStore.getState().setInspectMode(true);
    useInspectorStore.getState().addSelection({
      id: "desktop|1",
      selector: ".private-card",
      label: null,
      text: "A",
      html: null,
      comment: "",
    });
    useStyleEditStore.getState().scopeToProject("project-a", "session-a");
    useStyleEditStore
      .getState()
      .setElementProp(".private-card", "color", "#334455");

    useInspectorStore.getState().scopeToProject("project-b", "session-b");
    useStyleEditStore.getState().scopeToProject("project-b", "session-b");

    expect(useInspectorStore.getState()).toMatchObject({
      projectScope: "project-b",
      editorSession: "session-b",
      inspectMode: false,
      selections: [],
    });
    expect(useStyleEditStore.getState()).toMatchObject({
      projectScope: "project-b",
      editorSession: "session-b",
      elements: {},
      dirty: false,
    });
  });

  it("ignores cleanup from a replaced instance of the same project", () => {
    useInspectorStore.getState().scopeToProject("project-a", "session-old");
    useStyleEditStore.getState().scopeToProject("project-a", "session-old");
    useInspectorStore.getState().scopeToProject("project-a", "session-new");
    useStyleEditStore.getState().scopeToProject("project-a", "session-new");

    useInspectorStore
      .getState()
      .releaseProjectScope("project-a", "session-old");
    useStyleEditStore
      .getState()
      .releaseProjectScope("project-a", "session-old");

    expect(useInspectorStore.getState().editorSession).toBe("session-new");
    expect(useStyleEditStore.getState().editorSession).toBe("session-new");
  });

  it("stops style picking without closing the selected-element panel", () => {
    useStyleEditStore.getState().setStyleMode(true);
    useStyleEditStore.getState().selectElement({
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
