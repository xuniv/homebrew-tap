class Degoo < Formula
  desc "Command-line client for Degoo cloud storage"
  homepage "https://github.com/xuniv/homebrew-tap"
  version "0.1.0"
  license :cannot_represent

  on_macos do
    on_arm do
      url "https://github.com/xuniv/homebrew-tap/releases/download/degoo-v0.1.0/degoo-0.1.0-darwin-arm64.tar.gz"
      sha256 "a77553619cdc43df2f23efdec310ad64273ec0d541d3b6bc60578f1cfac87958"
    end
    on_intel do
      url "https://github.com/xuniv/homebrew-tap/releases/download/degoo-v0.1.0/degoo-0.1.0-darwin-amd64.tar.gz"
      sha256 "472afc87691b1af3fee4ad27a10a19bc643fc812c105c887b5631a6de0075b66"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/xuniv/homebrew-tap/releases/download/degoo-v0.1.0/degoo-0.1.0-linux-arm64.tar.gz"
      sha256 "b09111e2628c7154303a5dd0a092510ca58a8447b6ab265d80c37a16dce9823c"
    end
    on_intel do
      url "https://github.com/xuniv/homebrew-tap/releases/download/degoo-v0.1.0/degoo-0.1.0-linux-amd64.tar.gz"
      sha256 "1105b1d546f03fe592758b6af6f4d10c201b9cd4fa1fc598e50801b9ace57592"
    end
  end

  def install
    bin.install "degoo"
    pkgshare.install "LICENSES", "NOTICE.txt"
  end

  def post_install
    return unless OS.mac?

    executable = bin/"degoo"
    attributes = Utils.safe_popen_read("/usr/bin/xattr", executable).lines.map(&:strip)
    if attributes.include?("com.apple.quarantine")
      system "/usr/bin/xattr", "-d", "com.apple.quarantine", executable
    end
    system "/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", executable
    system "/usr/bin/codesign", "--verify", "--strict", executable
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/degoo version")
  end
end
