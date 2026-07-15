import "server-only";
import type { FeatureFlags } from "./features";

type FeatureEnvironment = Record<string, string | undefined>;

/** Server-only, fail-closed snapshot of every public UI entry-point flag. */
export function readFeatureFlags(environment: FeatureEnvironment = process.env): FeatureFlags {
  return {
    searchNavigation: environment.FEATURE_SEARCH_NAVIGATION === "true",
    storiesNavigation: environment.FEATURE_STORIES_NAVIGATION === "true",
    investigation: environment.FEATURE_INVESTIGATION === "true",
    signalDetail: environment.FEATURE_SIGNAL_DETAIL === "true",
  };
}
