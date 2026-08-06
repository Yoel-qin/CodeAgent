// react-cytoscapejs 无官方类型，最小可用声明（对齐 2.0.0 用法）。
declare module "react-cytoscapejs" {
  import type { Component } from "react";
  import type { Core, ElementDefinition, LayoutOptions } from "cytoscape";

  export interface CytoscapeComponentProps {
    elements?: ElementDefinition[];
    style?: React.CSSProperties;
    layout?: LayoutOptions;
    stylesheet?: unknown;
    cy?: (cy: Core) => void;
    pan?: { x: number; y: number };
    zoom?: number;
    minZoom?: number;
    maxZoom?: number;
    zoomingEnabled?: boolean;
    userZoomingEnabled?: boolean;
    panningEnabled?: boolean;
    userPanningEnabled?: boolean;
    boxSelectionEnabled?: boolean;
    autounselectify?: boolean;
    autoungrabify?: boolean;
    wheelSensitivity?: number;
    className?: string;
  }

  export default class CytoscapeComponent extends Component<CytoscapeComponentProps> {
    static createCytoscapeComponent(cy: unknown): typeof CytoscapeComponent;
  }
}
