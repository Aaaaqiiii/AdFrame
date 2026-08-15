type GenerationPreparation = {
  promptVersion: number
  publicUrl: string
  savePrompt: () => Promise<number>
  publishVideo: () => Promise<string>
  submit: (promptVersion: number) => Promise<string>
}

export async function runGenerationPreparation(input: GenerationPreparation): Promise<string> {
  const version = input.promptVersion || await input.savePrompt()
  if (!input.publicUrl) await input.publishVideo()
  return input.submit(version)
}
