export function resetCompletedLearningView(
  reset: () => void,
  clearGoal: (goal: string) => void,
  scrollTo: (options: ScrollToOptions) => void = (options) => window.scrollTo(options),
) {
  reset();
  clearGoal('');
  scrollTo({ top: 0, behavior: 'smooth' });
}
