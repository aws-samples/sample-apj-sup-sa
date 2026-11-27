export function HowItWorksPage() {
  return (
    <section className="how" aria-labelledby="how-title">
      <h2 id="how-title">How it works</h2>
      <p className="muted">
        Your prompt is sent to four models at once. Each one runs in its own Amazon Bedrock AgentCore Runtime session: the model writes a
        Blender Python script, headless Blender runs it and exports a GLB, and any Blender error goes back to the model to fix (up to three
        tries). Tokens are counted across every call, time is measured end to end, and cost uses each model's per-token price.
      </p>
      <p className="muted">
        Image to 3D works the same way with a photo instead of a prompt. The upload is scanned for malware by Amazon GuardDuty, Amazon
        Bedrock removes its background, and each model gets the cut-out image with instructions to match the object's shape, parts,
        colours and materials.
      </p>
      <a href="/architecture.png" target="_blank" rel="noreferrer" title="Open the full-size diagram">
        <img
          src="/architecture.png"
          alt="Reference architecture: CloudFront with AWS WAF serves the site and forwards the API to API Gateway, which only accepts CloudFront traffic; Lambda starts four parallel workers that call AgentCore Runtime, which uses Amazon Bedrock models and Blender; GLB files are stored in Amazon S3 and served through CloudFront, and metrics are kept in DynamoDB."
        />
      </a>
    </section>
  );
}
