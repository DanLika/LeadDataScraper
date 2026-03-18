## 2026-03-18 - AIChat Accessibility Improvements
**Learning:** The AIChat component relies on a 'div' element acting as a button, which is inaccessible to keyboard users, and multiple icon-only buttons lacked ARIA labels.
**Action:** Use native HTML buttons ('<button type="button">') for clickable actions and ensure all icon-only buttons include descriptive 'aria-label' attributes.
