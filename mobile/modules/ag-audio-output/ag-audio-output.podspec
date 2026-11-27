require "json"

package = JSON.parse(File.read(File.join(__dir__, "package.json")))

Pod::Spec.new do |s|
  s.name         = "ag-audio-output"
  s.version      = package["version"]
  s.summary      = package["description"]
  s.homepage     = "https://ag-projects.com"
  s.license      = package["license"]
  s.authors      = "AG Projects"
  s.platforms    = { :ios => "15.1" }
  s.source       = { :git => "https://github.com/AGProjects/ag-cute-music.git" }
  s.source_files = "ios/*.{h,m}"
  s.frameworks   = "AVKit", "AVFoundation"
  install_modules_dependencies(s)
end
