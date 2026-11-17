class Dclone < Formula
  desc "Rclone fork with a Degoo backend, installed as dclone"
  homepage "https://github.com/xuniv/homebrew-tap"
  version "1.76.0.1"
  license :cannot_represent

  on_macos do
    on_arm do
      url "https://github.com/xuniv/homebrew-tap/releases/download/dclone-v1.76.0.1/dclone-1.76.0.1-darwin-arm64.tar.gz"
      sha256 "bb0a867f3c934ebd76d4d55326f31de8356be165f0094382e70f2b05990a58e5"
    end
    on_intel do
      url "https://github.com/xuniv/homebrew-tap/releases/download/dclone-v1.76.0.1/dclone-1.76.0.1-darwin-amd64.tar.gz"
      sha256 "4417dadd4c9cef3871098dd09774f003f3a378e5e7e42e01c315b5b2d7d71894"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/xuniv/homebrew-tap/releases/download/dclone-v1.76.0.1/dclone-1.76.0.1-linux-arm64.tar.gz"
      sha256 "6db1aa753f7093f0c3a58d7f3fe1fa2d6b5c033c0f69560bfebad9339e917292"
    end
    on_intel do
      url "https://github.com/xuniv/homebrew-tap/releases/download/dclone-v1.76.0.1/dclone-1.76.0.1-linux-amd64.tar.gz"
      sha256 "c704f24545eb3aeca256140ea204930312e6313e7036367fcc91592d21c27bc6"
    end
  end

  def install
    bin.install "dclone"
    pkgshare.install "LICENSES", "NOTICE.txt"
  end

  def post_install
    return unless OS.mac?

    executable = bin/"dclone"
    attributes = Utils.safe_popen_read("/usr/bin/xattr", executable).lines.map(&:strip)
    if attributes.include?("com.apple.quarantine")
      system "/usr/bin/xattr", "-d", "com.apple.quarantine", executable
    end
    system "/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", executable
    system "/usr/bin/codesign", "--verify", "--strict", executable
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/dclone version")
  end
end
