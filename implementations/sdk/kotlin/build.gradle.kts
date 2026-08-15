plugins {
    kotlin("jvm") version "2.4.10"
    `maven-publish`
}

group = "com.spearsystems"
version = "0.1.0"

repositories { mavenCentral() }

dependencies {
    implementation("com.networknt:json-schema-validator:3.0.6")
    // Jackson 3 (tools.jackson) is used for both the SDK's own envelope/$id
    // parsing and networknt 3.x. 3.1.4 fixes CVE-2026-54512 and
    // CVE-2026-54513; the old Jackson 2 pins are gone with the 1.5.9 line.
    implementation("tools.jackson.core:jackson-databind:3.1.4")
    testImplementation(kotlin("test"))
}

kotlin { jvmToolchain(17) }

publishing {
    publications {
        create<MavenPublication>("mavenJava") {
            artifactId = "lcp-sdk-kotlin"
            from(components["java"])
            pom {
                name.set("LCP Kotlin SDK")
                description.set("LCP Lead Context Protocol SDK for Kotlin")
                url.set("https://github.com/SpearSystems/LCP")
                licenses { license { name.set("Apache-2.0"); url.set("https://www.apache.org/licenses/LICENSE-2.0") } }
            }
        }
    }
    repositories {
        maven {
            name = "release"
            url = uri(System.getenv("MAVEN_REPOSITORY_URL") ?: layout.buildDirectory.dir("repository"))
            credentials {
                username = System.getenv("MAVEN_USERNAME")
                password = System.getenv("MAVEN_PASSWORD")
            }
        }
    }
}
