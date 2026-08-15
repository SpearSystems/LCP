plugins {
    kotlin("jvm") version "2.4.10"
    `maven-publish`
}

group = "com.spearsystems"
version = "0.1.0"

repositories { mavenCentral() }

dependencies {
    implementation("com.networknt:json-schema-validator:1.5.9")
    // Pin Jackson above the patched 2.18.9 line: networknt 1.5.9 pulls 2.18.3,
    // which is affected by multiple CVEs fixed in 2.18.9. Explicit higher
    // versions win Gradle conflict resolution while staying on the 2.x API.
    implementation("com.fasterxml.jackson.core:jackson-core:2.18.9")
    implementation("com.fasterxml.jackson.core:jackson-databind:2.18.9")
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
