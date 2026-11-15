const SVG_NS = "http://www.w3.org/2000/svg";
const svgViews = new WeakMap();
const chartRanges = new WeakMap();

export function setText(node, text) {
  const next = String(text ?? "");
  if (node.textContent !== next) node.textContent = next;
}

export function svgRenderer(svg) {
  const nodes = svgViews.get(svg) ?? new Map();
  svgViews.set(svg, nodes);
  const order = [];
  return {
    node(key, tag, attributes, text) {
      let node = nodes.get(key);
      if (!node || node.localName !== tag) {
        node?.remove();
        node = document.createElementNS(SVG_NS, tag);
        nodes.set(key, node);
      }
      for (const [name, value] of Object.entries(attributes)) {
        const next = String(value);
        if (node.getAttribute(name) !== next) node.setAttribute(name, next);
      }
      if (text !== undefined) setText(node, text);
      order.push([key, node]);
      return node;
    },
    finish() {
      const active = new Set(order.map(([key]) => key));
      for (const [key, node] of nodes) {
        if (!active.has(key)) { node.remove(); nodes.delete(key); }
      }
      order.forEach(([, node], index) => {
        if (svg.children[index] !== node) svg.insertBefore(node, svg.children[index] ?? null);
      });
    },
  };
}

export function chartRange(svg, values, tick) {
  const previous = chartRanges.get(svg);
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (previous && tick >= previous.tick) {
    minimum = Math.min(minimum, previous.minimum);
    maximum = Math.max(maximum, previous.maximum);
  }
  const range = { minimum, maximum, tick };
  chartRanges.set(svg, range);
  return range;
}

export function resetChartRange(svg) { chartRanges.delete(svg); }
