export type ParticipantProfile = {
  displayName: string;
  avatarDataUrl: string | null;
};

export type ParticipantProfiles = {
  self: ParticipantProfile;
  other: ParticipantProfile;
};

function normalizedName(value: string, fallback: string): string {
  return value.trim() || fallback;
}

function cloneProfile(value: ParticipantProfile): ParticipantProfile {
  return {
    displayName: value.displayName,
    avatarDataUrl: value.avatarDataUrl,
  };
}

function mergeProfile(
  current: ParticipantProfile,
  incoming: ParticipantProfile,
): ParticipantProfile {
  const displayName = incoming.displayName.trim();
  const avatarDataUrl = incoming.avatarDataUrl?.trim() || null;
  return {
    displayName: displayName || current.displayName,
    avatarDataUrl: avatarDataUrl || current.avatarDataUrl,
  };
}

export function createParticipantProfiles(
  selfName: string,
  otherName: string,
): ParticipantProfiles {
  return {
    self: {
      displayName: normalizedName(selfName, "我"),
      avatarDataUrl: null,
    },
    other: {
      displayName: normalizedName(otherName, "Crush"),
      avatarDataUrl: null,
    },
  };
}

export function mergeParticipantProfiles(
  current: ParticipantProfiles,
  incoming: ParticipantProfiles,
): ParticipantProfiles {
  return {
    self: mergeProfile(current.self, incoming.self),
    other: mergeProfile(current.other, incoming.other),
  };
}

export function swapParticipantProfiles(
  value: ParticipantProfiles,
): ParticipantProfiles {
  return {
    self: cloneProfile(value.other),
    other: cloneProfile(value.self),
  };
}
