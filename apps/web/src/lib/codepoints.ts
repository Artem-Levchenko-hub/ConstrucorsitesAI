/** Match API/Python string lengths; never split a surrogate pair or trim a tail. */
export const characterCount = (value: string): number => Array.from(value).length;

export const withinCharacters = (value: string, maximum: number): boolean =>
  characterCount(value) <= maximum;
