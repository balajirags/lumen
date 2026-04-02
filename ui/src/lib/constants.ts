// Color palette for node labels — warm jewel tones + muted earth tones
const LABEL_COLORS: Record<string, string> = {
  Person: '#e07a5f',      // terra cotta
  Organization: '#3d405b', // charcoal blue
  Company: '#81b29a',     // sage green
  Project: '#f2cc8f',     // sandy gold
  Repository: '#b56576',  // dusty rose
  File: '#6d6875',        // muted mauve
  Folder: '#355070',      // deep slate blue
  Function: '#52b788',    // emerald
  Class: '#e9c46a',       // saffron
  Method: '#2a9d8f',      // teal
  Module: '#264653',      // dark teal
  Package: '#e76f51',     // burnt sienna
  Interface: '#a7c957',   // yellow-green
  Variable: '#bc6c25',    // amber brown
  Event: '#dda15e',       // warm tan
  Location: '#606c38',    // olive
  Document: '#8d99ae',    // cool grey
  Tag: '#d4a373',         // caramel
  Category: '#9b5de5',    // vivid purple
  User: '#00bbf9',        // sky blue
  Issue: '#f15bb5',       // hot pink
  PullRequest: '#00f5d4', // mint
  Commit: '#fee440',      // bright yellow
  Branch: '#9b2226',      // crimson
};

// Fallback palette for unknown labels (cycled)
const PALETTE = [
  '#e07a5f', '#81b29a', '#f2cc8f', '#b56576', '#355070',
  '#52b788', '#e9c46a', '#2a9d8f', '#e76f51', '#a7c957',
  '#bc6c25', '#dda15e', '#9b5de5', '#00bbf9', '#f15bb5',
  '#00f5d4', '#fee440', '#9b2226', '#606c38', '#d4a373',
];

const assignedColors = new Map<string, string>();
let paletteIdx = 0;

export function getLabelColor(label: string): string {
  if (LABEL_COLORS[label]) return LABEL_COLORS[label];
  if (assignedColors.has(label)) return assignedColors.get(label)!;
  const color = PALETTE[paletteIdx % PALETTE.length];
  paletteIdx++;
  assignedColors.set(label, color);
  return color;
}

// Edge type colors — warm/cool tones for visibility on dark backgrounds
const EDGE_COLORS: Record<string, string> = {
  CONTAINS: '#81b29a',   // sage green
  DEFINES: '#e9c46a',    // saffron
  IMPORTS: '#2a9d8f',    // teal
  CALLS: '#e07a5f',      // terra cotta
  EXTENDS: '#f2cc8f',    // sandy gold
  IMPLEMENTS: '#b56576',  // dusty rose
  DEPENDS_ON: '#355070', // deep slate blue
  KNOWS: '#52b788',      // emerald
  FOLLOWS: '#00bbf9',    // sky blue
  CREATED: '#dda15e',    // warm tan
  OWNS: '#e76f51',       // burnt sienna
  MEMBER_OF: '#9b5de5',  // vivid purple
  RELATES_TO: '#8d99ae', // cool grey
};

let edgePaletteIdx = 0;
const assignedEdgeColors = new Map<string, string>();

export function getEdgeColor(type: string): string {
  if (EDGE_COLORS[type]) return EDGE_COLORS[type];
  if (assignedEdgeColors.has(type)) return assignedEdgeColors.get(type)!;
  const color = PALETTE[edgePaletteIdx % PALETTE.length];
  edgePaletteIdx++;
  assignedEdgeColors.set(type, color);
  return color;
}
