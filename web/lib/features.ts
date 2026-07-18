export interface FeatureFlags {
  searchNavigation: boolean;
  storiesNavigation: boolean;
  investigation: boolean;
  signalDetail: boolean;
  earlyWarningRadar: boolean;
}

export const DEFAULT_FEATURE_FLAGS: FeatureFlags = {
  searchNavigation: false,
  storiesNavigation: false,
  investigation: false,
  signalDetail: false,
  earlyWarningRadar: false,
};
