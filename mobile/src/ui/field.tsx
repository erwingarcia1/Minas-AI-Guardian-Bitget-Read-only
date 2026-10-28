// A labelled text input, as the portal's .field draws one: the label above,
// the hint below, a 48-point touch target. The sign-in screen's, shared with
// accepting an invitation and setting up two-factor.
import { type ComponentRef, type Ref } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';

import { colors, radius, space, type } from '@/theme';

export type Input = ComponentRef<typeof TextInput>;

export function Field({
  label,
  hint,
  ref,
  ...input
}: { label: string; hint?: string; ref?: Ref<Input> } & Pick<
  React.ComponentProps<typeof TextInput>,
  | 'value'
  | 'onChangeText'
  | 'autoComplete'
  | 'keyboardType'
  | 'secureTextEntry'
  | 'returnKeyType'
  | 'submitBehavior'
  | 'onSubmitEditing'
  | 'autoCapitalize'
>) {
  return (
    <View style={{ gap: space(1) }}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        autoCapitalize="none"
        {...input}
        ref={ref}
        accessibilityLabel={label}
        accessibilityHint={hint}
        autoCorrect={false}
        placeholderTextColor={colors.textSubtle}
        style={styles.input}
      />
      {hint ? <Text style={styles.hint}>{hint}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  label: { color: colors.textMuted, fontSize: type.sm },
  hint: { color: colors.textMuted, fontSize: type.sm },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    color: colors.text,
    fontSize: type.base,
    paddingHorizontal: space(3),
  },
});
