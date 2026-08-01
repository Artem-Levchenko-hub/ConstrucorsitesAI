# Mobile data storytelling

Start with the decision, not the chart type.

- For every metric define: user question, unit, time window, comparison baseline,
  directionality (higher is not always better), confidence and action it enables.
- Put the conclusion beside the evidence. A number without context is decoration;
  a chart without a takeaway is a drawing.
- Use position and length for precise comparison. Reserve area, angle and colour
  for rough patterns. Do not use a pie chart for many categories or a line chart
  for unordered labels.
- On mobile, prefer a focused plot plus summary and detail-on-tap over a miniature
  desktop dashboard. Limit simultaneous series and keep labels outside the plot
  when possible.
- Preserve honest scales and baselines. Mark missing data, projected values,
  targets and anomalies explicitly.
- Use colour redundantly with labels, shape, stroke or pattern; never encode the
  only meaning as red versus green.
- Provide empty/insufficient-data states that explain what must happen before a
  trend or recommendation becomes valid.
- The MAX starter does not include a chart library. Use accessible semantic HTML,
  CSS or small purposeful SVG only when it remains legible and maintainable; do
  not invent a dependency.

For fitness/health scores, avoid presenting model output as medical certainty.
Expose the inputs and phrase recommendations proportionally to the evidence.
